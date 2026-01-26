"""
Area correlation profile.

Manages all scanner correlations for a single area, tracking
the typical RSSI relationships when a device is confirmed in that area.

Supports two types of learning:
1. Delta correlations: RSSI difference between primary and secondary scanners
2. Absolute profiles: Expected RSSI from each scanner (for fallback when primary offline)

Weighted Learning System:
    - Automatic learning: update() is capped at AUTO_SAMPLE_CAP per correlation
    - Button training: update_button() has stronger weight via BUTTON_WEIGHT
    - Button samples prevent auto from overwhelming manual training
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Self

from custom_components.bermuda.const import AUTO_LEARNING_MIN_CONFIDENCE, AUTO_LEARNING_MIN_INTERVAL

from .scanner_absolute import ScannerAbsoluteRssi
from .scanner_pair import ScannerPairCorrelation

# Memory limit: keep only the most useful correlations per area.
MAX_CORRELATIONS_PER_AREA: int = 15


@dataclass(slots=True)
class AreaProfile:
    """
    Collection of scanner correlations for one area.

    When a device is confirmed in this area, we track:
    1. RSSI delta from the primary scanner to all other visible scanners
    2. Absolute RSSI from each visible scanner (including primary)

    The delta correlations validate the current primary scanner choice.
    The absolute profiles enable fallback validation when primary goes offline.

    Example:
    -------
        When device is confirmed in "Büro" (office):
        - Delta: Primary-to-Scanner5 typically +30dB (relative)
        - Absolute: Scanner5 typically sees -85dB (absolute)

        If the Büro scanner goes offline but Scanner5 still shows -85dB,
        the device is likely still in Büro (pattern match).

    Attributes:
    ----------
        area_id: Home Assistant area ID (e.g., "area.living_room").

    """

    area_id: str
    _correlations: dict[str, ScannerPairCorrelation] = field(
        default_factory=dict,
        repr=False,
    )
    _absolute_profiles: dict[str, ScannerAbsoluteRssi] = field(
        default_factory=dict,
        repr=False,
    )
    # Timestamp of last auto-learning update (for minimum interval enforcement)
    _last_update_stamp: float = field(default=0.0, repr=False)

    def update(
        self,
        primary_rssi: float,
        other_readings: dict[str, float],
        primary_scanner_addr: str | None = None,
        nowstamp: float | None = None,
        last_stamps: dict[str, float] | None = None,
        current_stamps: dict[str, float] | None = None,
        confidence: float | None = None,
    ) -> bool:
        """
        Update correlations with new scanner readings (automatic learning).

        Called when a device is confirmed in this area. Updates:
        1. Delta correlations for each visible "other" scanner
        2. Absolute RSSI profiles for ALL visible scanners (including primary)

        Automatic samples are capped to prevent overwhelming button-trained data.
        Minimum interval enforcement reduces autocorrelation (rho: 0.95 to 0.82).

        Args:
        ----
            primary_rssi: RSSI from the winning (primary) scanner.
            other_readings: Map of scanner_address to RSSI for other scanners.
                           Must NOT include the primary scanner.
            primary_scanner_addr: Address of the primary scanner (for absolute tracking).
            nowstamp: Current timestamp for minimum interval enforcement.
                      If None, update always proceeds (backward compatibility).
            last_stamps: Previous advertisement timestamps per scanner (Feature 1).
                         If None, update always proceeds (first update).
            current_stamps: Current advertisement timestamps per scanner (Feature 1).
                            Used with last_stamps to detect new data.
            confidence: Area assignment confidence (0.0-1.0) for Feature 3.
                       If None, confidence check is skipped (backward compatibility).
                       If < AUTO_LEARNING_MIN_CONFIDENCE, update is rejected.

        Returns:
        -------
            True if update was performed, False if skipped due to minimum interval,
            no new advertisement data, or low confidence.

        """
        # Feature 3: Confidence Filter
        # Skip updates with low confidence to avoid polluting fingerprints with noise
        if confidence is not None and confidence < AUTO_LEARNING_MIN_CONFIDENCE:
            return False

        # Feature 1: New Data Check
        # Skip updates that re-read the same cached RSSI values (no new advertisements)
        if last_stamps is not None and current_stamps is not None:
            has_new_data = any(
                current_stamps.get(scanner, 0.0) > last_stamps.get(scanner, 0.0) for scanner in current_stamps
            )
            if not has_new_data:
                return False

        # Minimum Interval Check: Skip updates that are too frequent
        # This reduces autocorrelation from rho=0.95 to rho=0.82, improving ESS
        if nowstamp is not None:
            if nowstamp - self._last_update_stamp < AUTO_LEARNING_MIN_INTERVAL:
                return False
            self._last_update_stamp = nowstamp

        # Update delta correlations (existing behavior)
        for scanner_addr, rssi in other_readings.items():
            delta = primary_rssi - rssi

            if scanner_addr not in self._correlations:
                self._correlations[scanner_addr] = ScannerPairCorrelation(scanner_address=scanner_addr)

            self._correlations[scanner_addr].update(delta, timestamp=nowstamp)

        # Update absolute RSSI profiles for ALL visible scanners
        # This enables fallback validation when primary goes offline
        all_readings: dict[str, float] = dict(other_readings)
        if primary_scanner_addr is not None:
            all_readings[primary_scanner_addr] = primary_rssi

        for scanner_addr, rssi in all_readings.items():
            if scanner_addr not in self._absolute_profiles:
                self._absolute_profiles[scanner_addr] = ScannerAbsoluteRssi(scanner_address=scanner_addr)
            self._absolute_profiles[scanner_addr].update(rssi, timestamp=nowstamp)

        self._enforce_memory_limit()
        return True

    def update_button(
        self,
        primary_rssi: float,
        other_readings: dict[str, float],
        primary_scanner_addr: str | None = None,
        timestamp: float | None = None,
    ) -> None:
        """
        Update correlations with button-trained readings (stronger weight).

        Button samples have BUTTON_WEIGHT times the influence of automatic samples.
        This ensures manual room corrections are preserved against continuous
        automatic learning.

        Args:
        ----
            primary_rssi: RSSI from the winning (primary) scanner.
            other_readings: Map of scanner_address to RSSI for other scanners.
                           Must NOT include the primary scanner.
            primary_scanner_addr: Address of the primary scanner (for absolute tracking).
            timestamp: Optional timestamp for profile age tracking.

        """
        # Update delta correlations with button weight
        for scanner_addr, rssi in other_readings.items():
            delta = primary_rssi - rssi

            if scanner_addr not in self._correlations:
                self._correlations[scanner_addr] = ScannerPairCorrelation(scanner_address=scanner_addr)

            self._correlations[scanner_addr].update_button(delta, timestamp=timestamp)

        # Update absolute RSSI profiles with button weight
        all_readings: dict[str, float] = dict(other_readings)
        if primary_scanner_addr is not None:
            all_readings[primary_scanner_addr] = primary_rssi

        for scanner_addr, rssi in all_readings.items():
            if scanner_addr not in self._absolute_profiles:
                self._absolute_profiles[scanner_addr] = ScannerAbsoluteRssi(scanner_address=scanner_addr)
            self._absolute_profiles[scanner_addr].update_button(rssi, timestamp=timestamp)

        self._enforce_memory_limit()

    def reset_training(self) -> None:
        """
        Reset ALL learned data for this area (button AND auto).

        Clears both button and auto filter data from all correlations
        and absolute profiles. This provides a clean slate.

        Why reset both? The auto-learned data may be "poisoned" by incorrect
        room selection. After new button training, auto-learning will start
        fresh and learn patterns in the CORRECT context (via the indirect
        feedback loop where room selection influences what auto learns).

        Use this to completely undo incorrect training for a specific room.
        """
        for corr in self._correlations.values():
            corr.reset_training()
        for profile in self._absolute_profiles.values():
            profile.reset_training()

    def reset_variance_only(self) -> None:
        """
        Reset variance in all button filters while preserving estimates.

        Used for multi-position training within the same room. When starting
        a new training session, we want the new samples to have equal
        influence to previous training sessions, achieving true averaging
        across positions.

        Without this reset, subsequent training sessions would have
        diminishing influence due to the already-low variance from
        previous training (Kalman filter convergence).

        Example - 3 positions in a large room:
            Position 1: estimate=-75dB, variance converges to 3
            Position 2: Without reset, new samples have only ~10% influence!
            Position 2: With reset, variance=25, new samples have ~50% influence.
            Position 3: Same pattern - equal weighting across all positions.
        """
        for corr in self._correlations.values():
            corr.reset_variance_only()
        for profile in self._absolute_profiles.values():
            profile.reset_variance_only()

    def _enforce_memory_limit(self) -> None:
        """
        Evict least-important correlations if over memory limit.

        Sort priority (descending):
        1. has_button_training=True (NEVER evict user-trained profiles)
        2. sample_count (higher = more established)

        This ensures button-trained profiles for scannerless rooms are preserved
        even when auto-learned profiles accumulate more samples over time.
        """
        # Enforce limit for delta correlations
        if len(self._correlations) > MAX_CORRELATIONS_PER_AREA:
            sorted_corrs = sorted(
                self._correlations.items(),
                # Tuple sort: (True, 500) > (True, 100) > (False, 9999)
                key=lambda x: (x[1].has_button_training, x[1].sample_count),
                reverse=True,
            )
            self._correlations = dict(sorted_corrs[:MAX_CORRELATIONS_PER_AREA])

        # Enforce limit for absolute profiles (same logic)
        if len(self._absolute_profiles) > MAX_CORRELATIONS_PER_AREA:
            sorted_profiles = sorted(
                self._absolute_profiles.items(),
                key=lambda x: (x[1].has_button_training, x[1].sample_count),
                reverse=True,
            )
            self._absolute_profiles = dict(sorted_profiles[:MAX_CORRELATIONS_PER_AREA])

    def get_z_scores(
        self,
        primary_rssi: float,
        other_readings: dict[str, float],
    ) -> list[tuple[str, float]]:
        """
        Calculate z-scores for mature correlations.

        Compares current observations against learned expectations.
        Only includes correlations with enough samples to be reliable.

        Args:
        ----
            primary_rssi: Current RSSI from primary scanner.
            other_readings: Current RSSI from other scanners.

        Returns:
        -------
            List of (scanner_address, z_score) tuples.
            Empty if no mature correlations exist.

        """
        results: list[tuple[str, float]] = []

        for scanner_addr, rssi in other_readings.items():
            if scanner_addr not in self._correlations:
                continue

            corr = self._correlations[scanner_addr]
            if not corr.is_mature:
                continue

            observed_delta = primary_rssi - rssi
            z = corr.z_score(observed_delta)
            results.append((scanner_addr, z))

        return results

    def get_weighted_z_scores(
        self,
        primary_rssi: float,
        other_readings: dict[str, float],
    ) -> list[tuple[str, float, int]]:
        """
        Calculate z-scores with sample counts for weighted confidence.

        Args:
        ----
            primary_rssi: Current RSSI from primary scanner.
            other_readings: Current RSSI from other scanners.

        Returns:
        -------
            List of (scanner_address, z_score, sample_count) tuples.

        """
        results: list[tuple[str, float, int]] = []

        for scanner_addr, rssi in other_readings.items():
            if scanner_addr not in self._correlations:
                continue

            corr = self._correlations[scanner_addr]
            if not corr.is_mature:
                continue

            observed_delta = primary_rssi - rssi
            z = corr.z_score(observed_delta)
            results.append((scanner_addr, z, corr.sample_count))

        return results

    def get_absolute_rssi(self, scanner_addr: str) -> ScannerAbsoluteRssi | None:
        """
        Get the absolute RSSI profile for a specific scanner.

        Args:
        ----
            scanner_addr: MAC address of the scanner.

        Returns:
        -------
            ScannerAbsoluteRssi instance if it exists, None otherwise.

        """
        return self._absolute_profiles.get(scanner_addr)

    def get_absolute_z_scores(
        self,
        readings: dict[str, float],
    ) -> list[tuple[str, float]]:
        """
        Calculate z-scores for absolute RSSI profiles.

        Used for fallback validation when primary scanner is offline.
        Compares current RSSI readings against learned absolute expectations.

        Args:
        ----
            readings: Map of scanner_address to RSSI for all visible scanners.

        Returns:
        -------
            List of (scanner_address, z_score) tuples.
            Lower z-scores indicate better match with learned profile.

        """
        results: list[tuple[str, float]] = []

        for scanner_addr, rssi in readings.items():
            if scanner_addr not in self._absolute_profiles:
                continue

            profile = self._absolute_profiles[scanner_addr]
            if not profile.is_mature:
                continue

            z = profile.z_score(rssi)
            results.append((scanner_addr, z))

        return results

    def get_weighted_absolute_z_scores(
        self,
        readings: dict[str, float],
    ) -> list[tuple[str, float, int]]:
        """
        Calculate z-scores with sample counts for weighted confidence.

        Args:
        ----
            readings: Map of scanner_address to RSSI for all visible scanners.

        Returns:
        -------
            List of (scanner_address, z_score, sample_count) tuples.

        """
        results: list[tuple[str, float, int]] = []

        for scanner_addr, rssi in readings.items():
            if scanner_addr not in self._absolute_profiles:
                continue

            profile = self._absolute_profiles[scanner_addr]
            if not profile.is_mature:
                continue

            z = profile.z_score(rssi)
            results.append((scanner_addr, z, profile.sample_count))

        return results

    @property
    def mature_absolute_count(self) -> int:
        """Return number of absolute profiles with enough samples to trust."""
        return sum(1 for p in self._absolute_profiles.values() if p.is_mature)

    @property
    def correlation_count(self) -> int:
        """Return total number of tracked correlations."""
        return len(self._correlations)

    @property
    def mature_correlation_count(self) -> int:
        """Return number of correlations with enough samples to trust."""
        return sum(1 for c in self._correlations.values() if c.is_mature)

    @property
    def has_button_training(self) -> bool:
        """
        Check if this area profile has any button-trained data.

        Returns True if ANY of the absolute profiles or correlations
        have been button-trained by the user. This indicates explicit
        user intent to place a device in this area.
        """
        # Check absolute profiles first
        if any(profile.has_button_training for profile in self._absolute_profiles.values()):
            return True
        # Check delta correlations
        return any(corr.has_button_training for corr in self._correlations.values())

    @property
    def first_sample_stamp(self) -> float | None:
        """
        Return earliest timestamp from all child profiles.

        Aggregates the minimum first_sample_stamp from both correlations
        and absolute profiles. Returns None if no timestamps are available.

        Used for profile age tracking - when was this area profile first created.
        """
        timestamps: list[float] = [
            corr.first_sample_stamp for corr in self._correlations.values() if corr.first_sample_stamp is not None
        ]
        timestamps.extend(
            profile.first_sample_stamp
            for profile in self._absolute_profiles.values()
            if profile.first_sample_stamp is not None
        )

        return min(timestamps) if timestamps else None

    @property
    def last_sample_stamp(self) -> float | None:
        """
        Return latest timestamp from all child profiles.

        Aggregates the maximum last_sample_stamp from both correlations
        and absolute profiles. Returns None if no timestamps are available.

        Used for profile age tracking - when was this area profile last updated.
        """
        timestamps: list[float] = [
            corr.last_sample_stamp for corr in self._correlations.values() if corr.last_sample_stamp is not None
        ]
        timestamps.extend(
            profile.last_sample_stamp
            for profile in self._absolute_profiles.values()
            if profile.last_sample_stamp is not None
        )

        return max(timestamps) if timestamps else None

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize for persistent storage.

        Returns
        -------
            Dictionary with area_id and list of correlation dicts.

        """
        return {
            "area_id": self.area_id,
            "correlations": [c.to_dict() for c in self._correlations.values()],
            "absolute_profiles": [p.to_dict() for p in self._absolute_profiles.values()],
            "last_update_stamp": self._last_update_stamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """
        Deserialize from storage.

        Args:
        ----
            data: Dictionary from to_dict().

        Returns:
        -------
            Restored AreaProfile instance.

        """
        profile = cls(area_id=data["area_id"])
        # Restore delta correlations
        for corr_data in data.get("correlations", []):
            corr = ScannerPairCorrelation.from_dict(corr_data)
            profile._correlations[corr.scanner_address] = corr
        # Restore absolute profiles
        for profile_data in data.get("absolute_profiles", []):
            abs_profile = ScannerAbsoluteRssi.from_dict(profile_data)
            profile._absolute_profiles[abs_profile.scanner_address] = abs_profile
        # Restore last update timestamp (default 0.0 for backward compatibility)
        profile._last_update_stamp = data.get("last_update_stamp", 0.0)
        return profile
