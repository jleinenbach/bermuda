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
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bluetooth_data_tools import monotonic_time_coarse

from .const import (
    AREA_MAX_AD_AGE_DEFAULT,
    AREA_MAX_AD_AGE_LIMIT,
    CONF_MAX_RADIUS,
    CROSS_FLOOR_STREAK,
    DEFAULT_MAX_RADIUS,
    EVIDENCE_WINDOW_SECONDS,
    SAME_FLOOR_STREAK,
    UKF_LOW_CONFIDENCE_THRESHOLD,
    UKF_MIN_MATCH_SCORE,
    UKF_MIN_SCANNERS,
    UKF_RETENTION_THRESHOLD,
    UKF_RSSI_SANITY_MARGIN,
    UKF_STICKINESS_BONUS,
    UPDATE_INTERVAL,
    VIRTUAL_DISTANCE_MIN_SCORE,
    VIRTUAL_DISTANCE_SCALE,
)
from .correlation import AreaProfile, RoomProfile, z_scores_to_confidence
from .filters import UnscentedKalmanFilter

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
    # UKF-based area selection
    # =========================================================================

    def _apply_ukf_selection(
        self,
        device: BermudaDevice,
        best_advert: BermudaAdvert,
        best_area_id: str,
        *,
        scanner_less_room: bool,
        match_score: float,
        nowstamp: float,
    ) -> None:
        """Apply the UKF-selected area to the device and update correlations."""
        if scanner_less_room:
            # Override the advert's area with the UKF-matched area.
            # IMPORTANT: Temporarily clear scanner_device so apply_scanner_selection
            # uses our overridden area_id instead of scanner_device.area_id
            # (see bermuda_device.py apply_scanner_selection priority logic)
            #
            # FIX: BUG 8 - Save ALL modified attributes to restore after use
            # Without full restoration, the advert object remains "tainted" with
            # the virtual room's area_id, causing incorrect calibration data.
            saved_scanner_device = best_advert.scanner_device
            saved_area_id = best_advert.area_id
            saved_area_name = best_advert.area_name

            try:
                best_advert.scanner_device = None  # type: ignore[assignment]  # Temp for area override
                best_advert.area_id = best_area_id
                best_advert.area_name = self.resolve_area_name(best_area_id)

                device.apply_scanner_selection(best_advert, nowstamp=nowstamp)
            finally:
                # Restore ALL modified attributes to prevent dirty object state
                best_advert.scanner_device = saved_scanner_device
                best_advert.area_id = saved_area_id
                best_advert.area_name = saved_area_name

            # FIX: BUG 18 - Calculate virtual distance for scannerless rooms
            # Previously (BUG 13 fix) we set area_distance=None, but this shows "Unbekannt"
            # in the UI which confuses users. Instead, calculate a virtual distance from the
            # UKF match score using the same formula as _get_virtual_distances_for_scannerless_rooms.
            # Formula: distance = max_radius * VIRTUAL_DISTANCE_SCALE * (1 - score)²
            max_radius = self.options.get(CONF_MAX_RADIUS, DEFAULT_MAX_RADIUS)
            virtual_distance = max_radius * VIRTUAL_DISTANCE_SCALE * ((1.0 - match_score) ** 2)
            device.area_distance = virtual_distance
            device.area_distance_stamp = nowstamp
            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug(
                    "UKF scannerless room %s for %s: score=%.3f → virtual distance=%.2fm",
                    best_area_id,
                    device.name,
                    match_score,
                    virtual_distance,
                )
        else:
            # Apply the selection using the device's standard method
            device.apply_scanner_selection(best_advert, nowstamp=nowstamp)

        # AUTO-LEARNING: Update correlations so fingerprints adapt to environment changes
        # This mirrors the learning logic in _refresh_area_by_min_distance
        if best_advert.rssi is not None and best_area_id is not None:
            # Collect RSSI readings from other visible scanners
            other_readings: dict[str, float] = {}
            for other_adv in device.adverts.values():
                if (
                    other_adv is not best_advert
                    and other_adv.stamp is not None
                    and nowstamp - other_adv.stamp < EVIDENCE_WINDOW_SECONDS
                    and other_adv.rssi is not None
                    and other_adv.scanner_address is not None
                ):
                    other_readings[other_adv.scanner_address] = other_adv.rssi

            if other_readings:
                # Ensure device entry exists in correlations
                if device.address not in self.correlations:
                    self.correlations[device.address] = {}
                # Ensure area entry exists for this device
                if best_area_id not in self.correlations[device.address]:
                    self.correlations[device.address][best_area_id] = AreaProfile(
                        area_id=best_area_id,
                    )
                # Update the device-specific profile
                self.correlations[device.address][best_area_id].update(
                    primary_rssi=best_advert.rssi,
                    other_readings=other_readings,
                    primary_scanner_addr=best_advert.scanner_address,
                )

                # Also update the device-independent room profile
                all_readings = dict(other_readings)
                if best_advert.scanner_address is not None:
                    all_readings[best_advert.scanner_address] = best_advert.rssi
                if best_area_id not in self.room_profiles:
                    self.room_profiles[best_area_id] = RoomProfile(area_id=best_area_id)
                self.room_profiles[best_area_id].update(all_readings)

    def _refresh_area_by_ukf(self, device: BermudaDevice) -> bool:  # noqa: PLR0911, C901
        """
        Use UKF (Unscented Kalman Filter) for area selection via fingerprint matching.

        This method maintains a per-device UKF that fuses RSSI readings from all visible
        scanners. It then matches the fused state against learned area fingerprints to
        determine the most likely area.

        Returns True if a decision was made (area may or may not have changed),
        False if UKF cannot make a decision (e.g., insufficient scanners or profiles).
        """
        nowstamp = monotonic_time_coarse()

        # Collect RSSI readings from all visible scanners
        rssi_readings: dict[str, float] = {}
        for advert in device.adverts.values():
            if (
                advert.rssi is not None
                and advert.scanner_address is not None
                and advert.stamp is not None
                and nowstamp - advert.stamp < EVIDENCE_WINDOW_SECONDS
            ):
                rssi_readings[advert.scanner_address] = advert.rssi

        # Need minimum scanners for UKF to be useful
        # FIX: Bug 3 - Allow single-scanner RETENTION for scannerless rooms
        # In basements/isolated areas, often only 1 distant scanner sees the device.
        # If UKF requires 2 scanners, it bails out and min-distance takes over.
        # min_distance can't detect scannerless rooms → device jumps to scanner's room.
        #
        # Solution: For RETENTION (keeping current area), allow 1 scanner when:
        # 1. Device already has a confirmed area (device.area_id is not None)
        # 2. Device has trained profiles that include that area
        # 3. The single scanner's RSSI is consistent with the trained profile
        device_profiles = self.correlations.get(device.address, {})
        current_area_id = device.area_id

        # Check if this is a retention candidate with trained profiles
        can_retain_with_single_scanner = (
            len(rssi_readings) == 1 and current_area_id is not None and current_area_id in device_profiles
        )

        if len(rssi_readings) < UKF_MIN_SCANNERS and not can_retain_with_single_scanner:
            return False

        # Single-scanner retention: verify RSSI against trained profile
        if can_retain_with_single_scanner and current_area_id is not None:
            scanner_addr = next(iter(rssi_readings))
            current_rssi = rssi_readings[scanner_addr]
            area_profile = device_profiles.get(current_area_id)

            if area_profile is not None:
                # Check if the scanner has an absolute RSSI profile for this area
                abs_profile = area_profile.get_absolute_rssi(scanner_addr)
                if abs_profile is not None:
                    expected_rssi = abs_profile.expected_rssi
                    rssi_variance = abs_profile.variance
                    rssi_delta = abs(current_rssi - expected_rssi)

                    # Allow up to 3 standard deviations from expected
                    rssi_threshold = 3.0 * math.sqrt(max(rssi_variance, 4.0))

                    if rssi_delta <= rssi_threshold:
                        # RSSI matches profile - retain current area
                        if _LOGGER.isEnabledFor(logging.DEBUG):
                            _LOGGER.debug(
                                "UKF single-scanner retention for %s: "
                                "RSSI %.1f matches profile %.1f ± %.1f for area %s",
                                device.name,
                                current_rssi,
                                expected_rssi,
                                rssi_threshold,
                                current_area_id,
                            )
                        # Apply retention by updating the advert timestamp
                        if device.area_advert is not None:
                            device.apply_scanner_selection(device.area_advert, nowstamp=nowstamp)
                        return True
                    # RSSI doesn't match profile - fall back to min_distance
                    if _LOGGER.isEnabledFor(logging.DEBUG):
                        _LOGGER.debug(
                            "UKF single-scanner retention rejected for %s: RSSI %.1f too far from profile %.1f ± %.1f",
                            device.name,
                            current_rssi,
                            expected_rssi,
                            rssi_threshold,
                        )
            # No usable profile - fall back to min_distance
            return False

        # Get or create UKF for this device
        if device.address not in self.device_ukfs:
            self.device_ukfs[device.address] = UnscentedKalmanFilter()

        ukf = self.device_ukfs[device.address]

        # Update UKF with current measurements
        ukf.predict(dt=UPDATE_INTERVAL)
        ukf.update_multi(rssi_readings)

        # Device profiles already fetched above for single-scanner check

        # Need either device profiles or room profiles
        if not device_profiles and not self.room_profiles:
            return False

        # Match against both device-specific and room-level fingerprints
        matches = ukf.match_fingerprints(device_profiles, self.room_profiles)

        if not matches:
            return False

        # Get best match
        best_area_id, _d_squared, match_score = matches[0]

        # FIX: Sticky Virtual Rooms - Apply stickiness bonus for current area
        # When the device is already in an area (especially a scannerless one),
        # give that area a bonus to prevent marginal flickering.
        #
        # FIX: FEHLER 1 - MUST use device.area_id (confirmed system state), NOT device.area_advert.area_id!
        # device.area_advert is the LAST RECEIVED PACKET, which may be from ANY scanner.
        # For scannerless rooms: device is in "Virtual Room" but scanner in "Hallway" sends packet.
        # device.area_advert would point to "Hallway", giving stickiness bonus to WRONG room!
        # device.area_id is the CONFIRMED current location - the authoritative source of truth.
        # (current_area_id already fetched above for single-scanner check)

        # Check if current area is in the matches and apply stickiness
        effective_match_score = match_score
        current_area_match_score: float | None = None

        if current_area_id is not None:
            for area_id, _d_sq, score in matches:
                if area_id == current_area_id:
                    current_area_match_score = score
                    break

            # FIX: Sticky Virtual Rooms - If current area has a reasonable score,
            # it needs to be beaten by a significant margin
            if current_area_match_score is not None and best_area_id != current_area_id:
                # Apply stickiness: challenger must beat current by UKF_STICKINESS_BONUS
                stickiness_adjusted_current = current_area_match_score + UKF_STICKINESS_BONUS

                if match_score <= stickiness_adjusted_current:
                    # Current area wins with stickiness bonus
                    best_area_id = current_area_id
                    effective_match_score = current_area_match_score
                    if _LOGGER.isEnabledFor(logging.DEBUG):
                        _LOGGER.debug(
                            "UKF stickiness for %s: keeping %s (score=%.2f+%.2f bonus) over challenger (score=%.2f)",
                            device.name,
                            current_area_id,
                            current_area_match_score,
                            UKF_STICKINESS_BONUS,
                            match_score,
                        )

        # Check if match score meets minimum threshold (after stickiness adjustment)
        # FIX: FEHLER 3 - Use LOWER threshold for RETENTION (keeping current area)
        # When best_area_id == current_area_id (device would stay in same room), use a much
        # lower threshold (UKF_RETENTION_THRESHOLD) to prevent fallback to min-distance.
        # This keeps scannerless rooms "sticky" even with noisy/weak signals.
        # For SWITCHING to a NEW area, use the normal UKF_MIN_MATCH_SCORE threshold.
        is_retention = best_area_id == current_area_id and current_area_id is not None
        effective_threshold = UKF_RETENTION_THRESHOLD if is_retention else UKF_MIN_MATCH_SCORE

        if effective_match_score < effective_threshold:
            if is_retention:
                # FIX: FEHLER 3 - For retention case, return True even with low score
                # to prevent fallback to min-distance (which doesn't know scannerless rooms).
                # We still want to refresh the selection to update timestamps etc.
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug(
                        "UKF retention for %s: score %.2f < %.2f (retention threshold), "
                        "but keeping area %s to avoid min-distance fallback",
                        device.name,
                        effective_match_score,
                        effective_threshold,
                        current_area_id,
                    )
                if device.area_advert is not None:
                    device.apply_scanner_selection(device.area_advert, nowstamp=nowstamp)
                return True
            return False

        # Find the advert corresponding to the best area
        best_advert: BermudaAdvert | None = None
        for advert in device.adverts.values():
            if advert.area_id == best_area_id:
                best_advert = advert
                break

        if best_advert is None:
            # No current advert for the matched area - check if we can use any
            # advert with a scanner in that area
            for advert in device.adverts.values():
                if advert.scanner_device is not None:
                    scanner_area = getattr(advert.scanner_device, "area_id", None)
                    if scanner_area == best_area_id:
                        best_advert = advert
                        break

        scanner_less_room = False
        if best_advert is None:
            # Scanner-less room: UKF matched an area with no scanner.
            # Use the best available advert (strongest RSSI) and override its area.
            strongest_rssi = -999.0
            for advert in device.adverts.values():
                if (
                    advert.rssi is not None
                    and advert.stamp is not None
                    and nowstamp - advert.stamp < EVIDENCE_WINDOW_SECONDS
                    and advert.rssi > strongest_rssi
                ):
                    strongest_rssi = advert.rssi
                    best_advert = advert

            if best_advert is None:
                return False

            scanner_less_room = True

            # BUG 21 FIX: TOPOLOGICAL SANITY CHECK FOR SCANNERLESS ROOMS
            # When UKF picks a scannerless room on floor X, at least ONE scanner on floor X
            # must see the device. If NO scanner on the target floor sees the device,
            # the UKF decision is topologically impossible.
            #
            # This is robust because it doesn't rely on static RSSI thresholds which
            # vary by scanner/tracker hardware. Instead, it checks: "Is there ANY evidence
            # that the device is actually on this floor?"
            #
            # Example scenario this fixes:
            # - Device is in "Lagerraum" (basement, scannerless)
            # - UKF picks "Bad OG" (bathroom, 2 floors up, scannerless) with score 0.83
            # - But NO scanner on the "OG" floor sees the device
            # - Only basement scanners see the device
            # - Topologically impossible → reject UKF decision
            target_area_floor_id = self._resolve_floor_id_for_area(best_area_id)

            if target_area_floor_id is not None:
                # Check if any scanner on the target floor sees the device
                scanner_on_target_floor_sees_device = False

                for advert in device.adverts.values():
                    if (
                        advert.stamp is not None
                        and nowstamp - advert.stamp < EVIDENCE_WINDOW_SECONDS
                        and advert.scanner_device is not None
                    ):
                        scanner_floor_id = getattr(advert.scanner_device, "floor_id", None)
                        if scanner_floor_id == target_area_floor_id:
                            scanner_on_target_floor_sees_device = True
                            break

                if not scanner_on_target_floor_sees_device:
                    # UKF picked a scannerless room on a floor where NO scanner sees
                    # the device. This is topologically impossible.
                    if _LOGGER.isEnabledFor(logging.DEBUG):
                        _LOGGER.debug(
                            "BUG 21 FIX: UKF scannerless topological check FAILED for %s: "
                            "UKF picked %s (floor %s), but NO scanner on that floor sees "
                            "the device - falling back to min-distance",
                            device.name,
                            best_area_id,
                            target_area_floor_id,
                        )
                    return False

        # Track whether current area is scannerless (for stickiness in future cycles)
        device._ukf_scannerless_area = scanner_less_room  # type: ignore[attr-defined]  # noqa: SLF001

        # RSSI SANITY CHECK:
        # Only reject UKF decision if BOTH conditions are met:
        # 1. The selected room has significantly weaker signal (>15 dB)
        # 2. The UKF match score is borderline (< 0.6)
        #
        # If UKF has high confidence, trust it even with weaker signal - this allows
        # proper handling of scanner-less rooms and blocked/dampened scanners.
        # The fingerprint pattern is more reliable than raw RSSI in these cases.
        if not scanner_less_room and best_advert is not None:
            best_advert_rssi = best_advert.rssi
            strongest_visible_rssi = -999.0

            for advert in device.adverts.values():
                if (
                    advert.rssi is not None
                    and advert.stamp is not None
                    and nowstamp - advert.stamp < EVIDENCE_WINDOW_SECONDS
                    and advert.rssi > strongest_visible_rssi
                ):
                    strongest_visible_rssi = advert.rssi

            # Only apply sanity check when UKF confidence is low AND signal is much weaker
            if (
                effective_match_score < UKF_LOW_CONFIDENCE_THRESHOLD
                and best_advert_rssi is not None
                and strongest_visible_rssi > -999.0
                and strongest_visible_rssi - best_advert_rssi > UKF_RSSI_SANITY_MARGIN
            ):
                # Low confidence UKF picked a room with weak signal - suspicious
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug(
                        "UKF sanity check failed for %s: UKF picked %s (score=%.2f, RSSI %.1f) but "
                        "strongest signal is %.1f dB stronger - falling back to min-distance",
                        device.name,
                        best_area_id,
                        effective_match_score,
                        best_advert_rssi,
                        strongest_visible_rssi - best_advert_rssi,
                    )
                return False

        # DISTANCE-BASED SANITY CHECK (BUG 14):
        # When a device is VERY close to a scanner, it's almost certainly in that
        # scanner's room. UKF fingerprints can be wrong (bad training), but physical
        # distance doesn't lie. This prevents the bug where UKF picks a room 2 floors
        # away when the device is 1.6m from a scanner.
        #
        # Only reject UKF if:
        # 1. There's a scanner VERY close (<2m) to the device
        # 2. UKF picked a DIFFERENT area than that scanner's area
        # 3. The scanner has a valid area assigned
        proximity_threshold = 2.0  # meters - very close means almost certainly in that room
        nearest_scanner_distance = 999.0
        nearest_scanner_area_id: str | None = None
        nearest_scanner_floor_id: str | None = None

        for advert in device.adverts.values():
            if (
                advert.rssi_distance is not None
                and advert.stamp is not None
                and nowstamp - advert.stamp < EVIDENCE_WINDOW_SECONDS
                and advert.rssi_distance < nearest_scanner_distance
                and advert.scanner_device is not None
            ):
                scanner_area = getattr(advert.scanner_device, "area_id", None)
                if scanner_area is not None:
                    nearest_scanner_distance = advert.rssi_distance
                    nearest_scanner_area_id = scanner_area
                    nearest_scanner_floor_id = getattr(advert.scanner_device, "floor_id", None)

        if (
            nearest_scanner_distance < proximity_threshold
            and nearest_scanner_area_id is not None
            and nearest_scanner_area_id != best_area_id
        ):
            # Device is very close to a scanner but UKF picked a different room!
            # Check if this is a cross-floor decision (even more suspicious)
            ukf_floor_id = self._resolve_floor_id_for_area(best_area_id) if not scanner_less_room else None
            is_cross_floor_ukf = (
                nearest_scanner_floor_id is not None
                and ukf_floor_id is not None
                and nearest_scanner_floor_id != ukf_floor_id
            )

            if is_cross_floor_ukf:
                # UKF picked a room on a DIFFERENT floor while device is <2m from a scanner.
                # This is almost certainly wrong - fall back to min-distance.
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug(
                        "UKF distance sanity check FAILED for %s: Device is %.1fm from scanner "
                        "in %s (floor %s), but UKF picked %s (floor %s) - falling back to min-distance",
                        device.name,
                        nearest_scanner_distance,
                        nearest_scanner_area_id,
                        nearest_scanner_floor_id,
                        best_area_id,
                        ukf_floor_id,
                    )
                return False

            # Same floor but different room while very close - allow only with very high confidence
            if effective_match_score < 0.85:
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug(
                        "UKF distance sanity check FAILED for %s: Device is %.1fm from scanner "
                        "in %s, UKF picked %s with score %.2f < 0.85 - falling back to min-distance",
                        device.name,
                        nearest_scanner_distance,
                        nearest_scanner_area_id,
                        best_area_id,
                        effective_match_score,
                    )
                return False

        # CROSS-FLOOR STREAK PROTECTION:
        # Prevent rapid flickering between floors by requiring multiple consecutive
        # cycles picking the same target before allowing a cross-floor switch.
        #
        # FIX: FEHLER 1 (continued) - Use device.area_id for current area, NOT device.area_advert!
        # device.area_advert is the last received packet (may be from ANY scanner),
        # device.area_id is the CONFIRMED current location (authoritative source of truth).
        current_device_area_id = device.area_id

        # FIX: Unified Floor Guard - ALWAYS resolve floor_id from AREA, not from scanner_device
        # For scannerless rooms, the scanner_device belongs to a different area (and floor!)
        # entirely. We must ALWAYS look up the floor_id from the area registry directly to
        # ensure cross-floor protection works correctly for scannerless rooms.
        #
        # Bug fixed: Previously we tried scanner_device.floor_id first, which was WRONG for
        # scannerless rooms (e.g., device in "Office" Floor 1 but using scanner from
        # "Bedroom" Floor 2 would incorrectly think current floor was Floor 2).
        current_floor_id = None
        if current_device_area_id is not None:
            # FIX: Resolve floor from device.area_id (authoritative), not from advert's area
            current_floor_id = self._resolve_floor_id_for_area(current_device_area_id)

        # FIX: Unified Floor Guard - Resolve winner floor_id from TARGET AREA, not scanner
        # For scannerless rooms, best_advert.scanner_device is from a different room!
        winner_floor_id = None
        if scanner_less_room:
            # Scannerless room: get floor_id from the TARGET area (best_area_id)
            winner_floor_id = self._resolve_floor_id_for_area(best_area_id)
        elif best_advert is not None and best_advert.scanner_device is not None:
            # Scanner-based room: can use scanner_device's floor_id
            winner_floor_id = getattr(best_advert.scanner_device, "floor_id", None)

        is_cross_floor = (
            current_floor_id is not None and winner_floor_id is not None and current_floor_id != winner_floor_id
        )

        # If same area as current, just refresh the selection
        # FIX: FEHLER 1 (continued) - Compare with device.area_id, not current_area_advert.area_id
        if current_device_area_id is not None and best_area_id == current_device_area_id:
            device.reset_pending_state()
            self._apply_ukf_selection(
                device,
                best_advert,
                best_area_id,
                scanner_less_room=scanner_less_room,
                match_score=effective_match_score,
                nowstamp=nowstamp,
            )
            return True

        # If no current area, bootstrap immediately
        # FIX: FEHLER 1 (continued) - Check device.area_id, not current_area_advert
        if current_device_area_id is None:
            device.reset_pending_state()
            self._apply_ukf_selection(
                device,
                best_advert,
                best_area_id,
                scanner_less_room=scanner_less_room,
                match_score=effective_match_score,
                nowstamp=nowstamp,
            )
            return True

        # Determine streak target based on floor change
        streak_target = CROSS_FLOOR_STREAK if is_cross_floor else SAME_FLOOR_STREAK

        # BUG 20 FIX: Only count streak if we have NEW advertisement data
        # Similar to BUG 19 fix for training - prevents cached values from being
        # counted multiple times. BLE devices advertise every 1-10s, but coordinator
        # updates every ~1s. Without this check, the same cached RSSI would count
        # as multiple "votes" for a room switch.
        current_stamps = self._collect_current_stamps(device, nowstamp)
        has_new_data = self._has_new_advert_data(current_stamps, device.pending_last_stamps)

        # Update streak counter - only if we have new data
        if device.pending_area_id == best_area_id and device.pending_floor_id == winner_floor_id:
            # Same target as before - only increment if new data
            if has_new_data:
                device.pending_streak += 1
                device.pending_last_stamps = dict(current_stamps)
            # If no new data, keep current streak count (don't increment)
        elif device.pending_area_id is not None and device.pending_area_id != best_area_id:
            # Different target - reset streak to new candidate
            device.pending_area_id = best_area_id
            device.pending_floor_id = winner_floor_id
            device.pending_streak = 1
            device.pending_last_stamps = dict(current_stamps)
        else:
            # First pending or same floor different area
            device.pending_area_id = best_area_id
            device.pending_floor_id = winner_floor_id
            device.pending_streak = 1
            device.pending_last_stamps = dict(current_stamps)

        # Check if streak meets threshold
        if device.pending_streak >= streak_target:
            device.reset_pending_state()
            self._apply_ukf_selection(
                device,
                best_advert,
                best_area_id,
                scanner_less_room=scanner_less_room,
                match_score=effective_match_score,
                nowstamp=nowstamp,
            )
        # Streak not reached - keep current area
        # NOTE: Use device.area_advert here (the actual advert object), not current_device_area_id.
        # apply_scanner_selection needs an advert object. The area_id determination above
        # correctly uses device.area_id, but the actual selection still needs the advert.
        elif device.area_advert is not None:
            device.apply_scanner_selection(device.area_advert, nowstamp=nowstamp)

        return True

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
