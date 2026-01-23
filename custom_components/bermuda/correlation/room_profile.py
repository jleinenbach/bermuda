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

Weighted Learning System:
    - Automatic learning: update() is capped at AUTO_SAMPLE_CAP per scanner pair
    - Button training: update_button() has stronger weight via BUTTON_WEIGHT
    - Button samples prevent auto from overwhelming manual training
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Self

from .scanner_pair import ScannerPairCorrelation

# Memory limit: keep only the most useful scanner pairs.
MAX_SCANNER_PAIRS_PER_ROOM: int = 20

# Z-score threshold for match score calculation.
# At this z-score, confidence drops to 0.5 (uncertain).
# Uses 2.0 (stricter than confidence.py's 2.5) because room-level
# matching with multiple scanner pairs can tolerate tighter bounds -
# the averaging across pairs reduces noise compared to single-pair matching.
MATCH_SCORE_Z_THRESHOLD: float = 2.0


def _make_pair_key(scanner_a: str, scanner_b: str) -> str:
    """
    Create consistent key for a scanner pair.

    Always orders alphabetically to avoid duplicates.
    """
    if scanner_a < scanner_b:
        return f"{scanner_a}|{scanner_b}"
    return f"{scanner_b}|{scanner_a}"


@dataclass(slots=True)
class RoomProfile:
    """
    Device-independent room fingerprint based on scanner-pair deltas.

    Reuses ScannerPairCorrelation for Kalman-filtered delta tracking.
    """

    area_id: str
    _scanner_pairs: dict[str, ScannerPairCorrelation] = field(
        default_factory=dict,
        repr=False,
    )

    def update(self, readings: dict[str, float]) -> None:
        """
        Update room profile with RSSI readings from automatic learning.

        Automatic samples are capped at AUTO_SAMPLE_CAP per scanner pair
        to prevent overwhelming button-trained data.

        Args:
            readings: Map of scanner_address to RSSI value.

        """
        scanner_list = list(readings.keys())

        for i, first in enumerate(scanner_list):
            for second in scanner_list[i + 1 :]:
                # Consistent ordering (alphabetically)
                addr_a, addr_b = (first, second) if first < second else (second, first)
                pair_key = _make_pair_key(addr_a, addr_b)

                if pair_key not in self._scanner_pairs:
                    self._scanner_pairs[pair_key] = ScannerPairCorrelation(
                        scanner_address=pair_key  # Store key as "address"
                    )

                # Delta: first alphabetically - second alphabetically
                delta = readings[addr_a] - readings[addr_b]
                self._scanner_pairs[pair_key].update(delta)

        self._enforce_memory_limit()

    def update_button(self, readings: dict[str, float]) -> None:
        """
        Update room profile with RSSI readings from button training (stronger weight).

        Button samples have BUTTON_WEIGHT times the influence of automatic samples.
        This ensures manual room corrections are preserved against continuous
        automatic learning.

        Args:
            readings: Map of scanner_address to RSSI value.

        """
        scanner_list = list(readings.keys())

        for i, first in enumerate(scanner_list):
            for second in scanner_list[i + 1 :]:
                # Consistent ordering (alphabetically)
                addr_a, addr_b = (first, second) if first < second else (second, first)
                pair_key = _make_pair_key(addr_a, addr_b)

                if pair_key not in self._scanner_pairs:
                    self._scanner_pairs[pair_key] = ScannerPairCorrelation(
                        scanner_address=pair_key  # Store key as "address"
                    )

                # Delta: first alphabetically - second alphabetically
                delta = readings[addr_a] - readings[addr_b]
                self._scanner_pairs[pair_key].update_button(delta)

        self._enforce_memory_limit()

    def _enforce_memory_limit(self) -> None:
        """
        Evict least-important scanner pairs if over memory limit.

        Sort priority (descending):
        1. has_button_training=True (NEVER evict user-trained pairs)
        2. sample_count (higher = more established)

        This ensures button-trained pairs for scannerless rooms are preserved
        even when auto-learned pairs accumulate more samples over time.
        """
        if len(self._scanner_pairs) > MAX_SCANNER_PAIRS_PER_ROOM:
            sorted_pairs = sorted(
                self._scanner_pairs.items(),
                # Tuple sort: (True, 500) > (True, 100) > (False, 9999)
                key=lambda x: (x[1].has_button_training, x[1].sample_count),
                reverse=True,
            )
            self._scanner_pairs = dict(sorted_pairs[:MAX_SCANNER_PAIRS_PER_ROOM])

    def reset_training(self) -> None:
        """
        Reset ALL learned data for this room (button AND auto).

        This provides a clean slate - after reset, new button training
        will establish correct patterns, and auto-learning will start
        fresh in the correct context (via indirect feedback loop).

        Use this to undo incorrect training without residual "poisoned" data.
        """
        for pair in self._scanner_pairs.values():
            pair.reset_training()

    @property
    def has_button_training(self) -> bool:
        """
        Check if this room profile has any button-trained data.

        Returns True if ANY of the scanner pairs have been button-trained
        by the user. This indicates explicit user intent for this room.
        """
        return any(pair.has_button_training for pair in self._scanner_pairs.values())

    def get_match_score(self, readings: dict[str, float]) -> float:
        """
        Calculate how well current readings match this room's profile.

        Returns:
            Score from 0.0 (no match) to 1.0 (perfect match).
            Returns 0.5 if no mature pairs to compare.

        """
        z_scores: list[float] = []
        weights: list[int] = []
        scanner_list = list(readings.keys())

        for i, first in enumerate(scanner_list):
            for second in scanner_list[i + 1 :]:
                addr_a, addr_b = (first, second) if first < second else (second, first)
                pair_key = _make_pair_key(addr_a, addr_b)

                if pair_key not in self._scanner_pairs:
                    continue

                pair = self._scanner_pairs[pair_key]
                if not pair.is_mature:
                    continue

                delta = readings[addr_a] - readings[addr_b]
                z_scores.append(pair.z_score(delta))
                weights.append(pair.sample_count)

        if not z_scores:
            return 0.5  # No data, neutral

        # Weighted average z-score → confidence using Cauchy-like sigmoid
        total_weight = sum(weights)
        weighted_z = sum(z * w for z, w in zip(z_scores, weights, strict=True)) / total_weight
        return 1.0 / (1.0 + (weighted_z / MATCH_SCORE_Z_THRESHOLD) ** 2)

    @property
    def total_samples(self) -> int:
        """Return sum of samples across all pairs."""
        return sum(p.sample_count for p in self._scanner_pairs.values())

    @property
    def mature_pair_count(self) -> int:
        """Return number of scanner pairs with enough data to trust."""
        return sum(1 for p in self._scanner_pairs.values() if p.is_mature)

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
            pair = ScannerPairCorrelation.from_dict(pair_data)
            profile._scanner_pairs[pair.scanner_address] = pair
        return profile
