"""
Area correlation profile.

Manages all scanner correlations for a single area, tracking
the typical RSSI relationships when a device is confirmed in that area.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Self

from .scanner_pair import ScannerPairCorrelation

# Memory limit: keep only the most useful correlations per area.
MAX_CORRELATIONS_PER_AREA: int = 15


@dataclass(slots=True)
class AreaProfile:
    """
    Collection of scanner correlations for one area.

    When a device is confirmed in this area, we track the RSSI delta
    from the primary (winning) scanner to all other visible scanners.

    Over time, this builds a "fingerprint" of what the RSSI pattern
    looks like when a device is truly in this area.

    Attributes:
        area_id: Home Assistant area ID (e.g., "area.living_room").

    """

    area_id: str
    _correlations: dict[str, ScannerPairCorrelation] = field(
        default_factory=dict,
        repr=False,
    )

    def update(
        self,
        primary_rssi: float,
        other_readings: dict[str, float],
    ) -> None:
        """
        Update correlations with new scanner readings.

        Called when a device is confirmed in this area. Updates the
        learned delta for each visible "other" scanner.

        Args:
            primary_rssi: RSSI from the winning (primary) scanner.
            other_readings: Map of scanner_address to RSSI for other scanners.
                           Must NOT include the primary scanner.

        """
        for scanner_addr, rssi in other_readings.items():
            delta = primary_rssi - rssi

            if scanner_addr not in self._correlations:
                self._correlations[scanner_addr] = ScannerPairCorrelation(scanner_address=scanner_addr)

            self._correlations[scanner_addr].update(delta)

        self._enforce_memory_limit()

    def _enforce_memory_limit(self) -> None:
        """Evict least-sampled correlations if over memory limit."""
        if len(self._correlations) <= MAX_CORRELATIONS_PER_AREA:
            return

        # Keep correlations with most samples (most reliable)
        sorted_corrs = sorted(
            self._correlations.items(),
            key=lambda x: x[1].sample_count,
            reverse=True,
        )
        self._correlations = dict(sorted_corrs[:MAX_CORRELATIONS_PER_AREA])

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
            primary_rssi: Current RSSI from primary scanner.
            other_readings: Current RSSI from other scanners.

        Returns:
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
            primary_rssi: Current RSSI from primary scanner.
            other_readings: Current RSSI from other scanners.

        Returns:
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

    @property
    def correlation_count(self) -> int:
        """Return total number of tracked correlations."""
        return len(self._correlations)

    @property
    def mature_correlation_count(self) -> int:
        """Return number of correlations with enough samples to trust."""
        return sum(1 for c in self._correlations.values() if c.is_mature)

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize for persistent storage.

        Returns:
            Dictionary with area_id and list of correlation dicts.

        """
        return {
            "area_id": self.area_id,
            "correlations": [c.to_dict() for c in self._correlations.values()],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """
        Deserialize from storage.

        Args:
            data: Dictionary from to_dict().

        Returns:
            Restored AreaProfile instance.

        """
        profile = cls(area_id=data["area_id"])
        for corr_data in data.get("correlations", []):
            corr = ScannerPairCorrelation.from_dict(corr_data)
            profile._correlations[corr.scanner_address] = corr
        return profile
