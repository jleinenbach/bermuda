"""
Room-level correlation profile (device-independent).

Stores scanner-pair delta patterns that are shared across all devices.
Since the RSSI delta between two scanners in a specific location should be
the same regardless of which device is measuring, this enables:

1. Faster learning: All devices contribute to the same room profile
2. Immediate benefit for new devices: Use room profile as fallback
3. Better accuracy for rooms without their own scanner

The key insight is that while absolute RSSI varies by device (different
transmit power, antenna characteristics), the RELATIVE difference between
two scanners should be consistent for any device in the same location.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Self

from custom_components.bermuda.filters.kalman import KalmanFilter

# Kalman parameters for scanner-pair delta tracking.
# Deltas should be very stable (physical layout doesn't change).
ROOM_DELTA_PROCESS_NOISE: float = 0.3

# Some variation expected from multipath, interference, furniture.
ROOM_DELTA_MEASUREMENT_NOISE: float = 12.0

# Need enough samples to be statistically reliable.
ROOM_MIN_SAMPLES_FOR_MATURITY: int = 20

# Memory limit: keep only the most useful scanner pairs.
MAX_SCANNER_PAIRS_PER_ROOM: int = 20


def _make_pair_key(scanner_a: str, scanner_b: str) -> str:
    """
    Create consistent key for a scanner pair.

    Always orders alphabetically to avoid duplicates:
    (A, B) and (B, A) both become "A|B"

    Args:
        scanner_a: First scanner address.
        scanner_b: Second scanner address.

    Returns:
        Pipe-separated string with scanners in alphabetical order.

    """
    if scanner_a < scanner_b:
        return f"{scanner_a}|{scanner_b}"
    return f"{scanner_b}|{scanner_a}"


def _parse_pair_key(key: str) -> tuple[str, str]:
    """
    Parse scanner pair key back to addresses.

    Args:
        key: Pipe-separated scanner addresses.

    Returns:
        Tuple of (scanner_a, scanner_b) in alphabetical order.

    """
    parts = key.split("|")
    return (parts[0], parts[1])


@dataclass(slots=True)
class ScannerPairDelta:
    """
    Tracks expected RSSI delta between two scanners in a room.

    The delta is always calculated as: first_scanner - second_scanner
    where first_scanner is alphabetically before second_scanner.

    This ensures consistent calculation regardless of which scanner
    is "primary" at any given moment.

    Attributes:
        scanner_a: First scanner address (alphabetically).
        scanner_b: Second scanner address (alphabetically).

    """

    scanner_a: str
    scanner_b: str
    _kalman: KalmanFilter = field(
        default_factory=lambda: KalmanFilter(
            process_noise=ROOM_DELTA_PROCESS_NOISE,
            measurement_noise=ROOM_DELTA_MEASUREMENT_NOISE,
        ),
        repr=False,
    )

    def update(self, rssi_a: float, rssi_b: float) -> float:
        """
        Update with new RSSI readings from both scanners.

        Args:
            rssi_a: RSSI from scanner_a (alphabetically first).
            rssi_b: RSSI from scanner_b (alphabetically second).

        Returns:
            Updated Kalman estimate of expected delta.

        """
        delta = rssi_a - rssi_b
        return self._kalman.update(delta)

    @property
    def expected_delta(self) -> float:
        """Return learned expected delta (rssi_a - rssi_b)."""
        return self._kalman.estimate

    @property
    def variance(self) -> float:
        """Return current uncertainty (variance) in the estimate."""
        return self._kalman.variance

    @property
    def std_dev(self) -> float:
        """Return standard deviation of the estimate."""
        return float(self.variance**0.5)

    @property
    def sample_count(self) -> int:
        """Return number of samples processed."""
        return self._kalman.sample_count

    @property
    def is_mature(self) -> bool:
        """Check if enough samples for reliable estimate."""
        return self.sample_count >= ROOM_MIN_SAMPLES_FOR_MATURITY

    def z_score(self, rssi_a: float, rssi_b: float) -> float:
        """
        Calculate how many standard deviations observed delta differs from expected.

        Args:
            rssi_a: Current RSSI from scanner_a.
            rssi_b: Current RSSI from scanner_b.

        Returns:
            Absolute z-score. Lower values indicate better match.

        """
        if self.variance <= 0:
            return 0.0
        observed_delta = rssi_a - rssi_b
        return abs(observed_delta - self.expected_delta) / self.std_dev

    def to_dict(self) -> dict[str, Any]:
        """Serialize for storage."""
        return {
            "scanner_a": self.scanner_a,
            "scanner_b": self.scanner_b,
            "estimate": self._kalman.estimate,
            "variance": self._kalman.variance,
            "samples": self._kalman.sample_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Deserialize from storage."""
        pair = cls(scanner_a=data["scanner_a"], scanner_b=data["scanner_b"])
        pair._kalman.estimate = data["estimate"]
        pair._kalman.variance = data["variance"]
        pair._kalman.sample_count = data["samples"]
        pair._kalman._initialized = data["samples"] > 0  # noqa: SLF001
        return pair


@dataclass(slots=True)
class RoomProfile:
    """
    Device-independent room fingerprint based on scanner-pair deltas.

    Tracks the typical RSSI differences between all pairs of scanners
    visible in a room. Since these deltas are device-independent,
    all devices contribute to and benefit from the same profile.

    Example:
        In "Kitchen":
        - Scanner_A - Scanner_B typically = +15dB
        - Scanner_A - Scanner_C typically = +8dB
        - Scanner_B - Scanner_C typically = -7dB

        Any device in the kitchen should see similar deltas,
        regardless of its own transmit power or antenna.

    Attributes:
        area_id: Home Assistant area ID.

    """

    area_id: str
    _scanner_pairs: dict[str, ScannerPairDelta] = field(
        default_factory=dict,
        repr=False,
    )

    def update(self, readings: dict[str, float]) -> None:
        """
        Update room profile with RSSI readings from multiple scanners.

        Creates/updates delta tracking for all pairs of visible scanners.

        Args:
            readings: Map of scanner_address to RSSI value.

        """
        # Get list of scanners for pairwise comparison
        scanner_list = list(readings.keys())

        # Update all pairs
        for i, first in enumerate(scanner_list):
            for second in scanner_list[i + 1 :]:
                # Ensure consistent ordering (alphabetically)
                addr_a, addr_b = (first, second) if first < second else (second, first)

                pair_key = _make_pair_key(addr_a, addr_b)

                if pair_key not in self._scanner_pairs:
                    self._scanner_pairs[pair_key] = ScannerPairDelta(
                        scanner_a=addr_a,
                        scanner_b=addr_b,
                    )

                self._scanner_pairs[pair_key].update(
                    rssi_a=readings[addr_a],
                    rssi_b=readings[addr_b],
                )

        self._enforce_memory_limit()

    def _enforce_memory_limit(self) -> None:
        """Evict least-sampled pairs if over memory limit."""
        if len(self._scanner_pairs) > MAX_SCANNER_PAIRS_PER_ROOM:
            sorted_pairs = sorted(
                self._scanner_pairs.items(),
                key=lambda x: x[1].sample_count,
                reverse=True,
            )
            self._scanner_pairs = dict(sorted_pairs[:MAX_SCANNER_PAIRS_PER_ROOM])

    def get_match_score(self, readings: dict[str, float]) -> float:
        """
        Calculate how well current readings match this room's profile.

        Uses z-scores across all mature scanner pairs to determine
        if the observed deltas match the learned pattern.

        Args:
            readings: Map of scanner_address to RSSI value.

        Returns:
            Score from 0.0 (no match) to 1.0 (perfect match).
            Returns 0.5 if no mature pairs to compare.

        """
        z_scores: list[float] = []
        weights: list[int] = []

        scanner_list = list(readings.keys())

        for i, first in enumerate(scanner_list):
            for second in scanner_list[i + 1 :]:
                # Ensure consistent ordering (alphabetically)
                addr_a, addr_b = (first, second) if first < second else (second, first)

                pair_key = _make_pair_key(addr_a, addr_b)

                if pair_key not in self._scanner_pairs:
                    continue

                pair = self._scanner_pairs[pair_key]
                if not pair.is_mature:
                    continue

                z = pair.z_score(readings[addr_a], readings[addr_b])
                z_scores.append(z)
                weights.append(pair.sample_count)

        if not z_scores:
            return 0.5  # No data, neutral score

        # Weighted average z-score
        total_weight = sum(weights)
        weighted_z = sum(z * w for z, w in zip(z_scores, weights, strict=True)) / total_weight

        # Convert z-score to confidence (0-1)
        # z=0 → 1.0, z=2 → 0.5, z=4 → ~0.1
        return 1.0 / (1.0 + (weighted_z / 2.0) ** 2)

    def get_z_scores(self, readings: dict[str, float]) -> list[tuple[str, float, int]]:
        """
        Get individual z-scores for all mature scanner pairs.

        Args:
            readings: Map of scanner_address to RSSI value.

        Returns:
            List of (pair_key, z_score, sample_count) tuples.

        """
        results: list[tuple[str, float, int]] = []
        scanner_list = list(readings.keys())

        for i, first in enumerate(scanner_list):
            for second in scanner_list[i + 1 :]:
                # Ensure consistent ordering (alphabetically)
                addr_a, addr_b = (first, second) if first < second else (second, first)

                pair_key = _make_pair_key(addr_a, addr_b)

                if pair_key not in self._scanner_pairs:
                    continue

                pair = self._scanner_pairs[pair_key]
                if not pair.is_mature:
                    continue

                z = pair.z_score(readings[addr_a], readings[addr_b])
                results.append((pair_key, z, pair.sample_count))

        return results

    @property
    def total_samples(self) -> int:
        """Return sum of samples across all pairs."""
        return sum(p.sample_count for p in self._scanner_pairs.values())

    @property
    def mature_pair_count(self) -> int:
        """Return number of scanner pairs with enough data to trust."""
        return sum(1 for p in self._scanner_pairs.values() if p.is_mature)

    @property
    def pair_count(self) -> int:
        """Return total number of tracked scanner pairs."""
        return len(self._scanner_pairs)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for storage."""
        return {
            "area_id": self.area_id,
            "scanner_pairs": [p.to_dict() for p in self._scanner_pairs.values()],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Deserialize from storage."""
        profile = cls(area_id=data["area_id"])
        for pair_data in data.get("scanner_pairs", []):
            pair = ScannerPairDelta.from_dict(pair_data)
            pair_key = _make_pair_key(pair.scanner_a, pair.scanner_b)
            profile._scanner_pairs[pair_key] = pair
        return profile
