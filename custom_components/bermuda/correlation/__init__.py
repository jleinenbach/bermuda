"""
Scanner correlation learning for improved area localization.

This module learns typical RSSI relationships between scanners for each area.
When the observed pattern doesn't match the learned pattern, it suggests
the device might not actually be in the suspected area.

Two types of learning:
    - Automatic learning: update() called continuously, capped influence
    - Button training: update_button() from user action, sets the "anchor"

Clamped Bayesian Fusion ensures user training retains at least 70% authority
while auto-learning can "polish" the anchor with max 30% influence.

Example usage in coordinator::

    from .correlation import AreaProfile, CorrelationStore, RoomProfile

    # Automatic learning (on confirmed area selection):
    area_profile.update(primary_rssi, other_readings, primary_scanner_addr)
    room_profile.update(all_rssi_readings)

    # Button training (user explicitly trains a room):
    area_profile.update_button(primary_rssi, other_readings, primary_scanner_addr)
    room_profile.update_button(all_rssi_readings)

    # When evaluating area candidates:
    z_scores = area_profile.get_weighted_z_scores(primary_rssi, other_readings)
    confidence = weighted_z_scores_to_confidence(z_scores)

    # Check if profile has user-trained data:
    if area_profile.has_button_training:
        # User explicitly trained this room - trust it

    # Reset ALL training (button AND auto) for a room:
    area_profile.reset_training()
    room_profile.reset_training()

Architecture:
    - ScannerPairCorrelation: Kalman-filtered delta tracker (primary-to-other)
    - ScannerAbsoluteRssi: Kalman-filtered absolute RSSI tracker (per-scanner)
    - AreaProfile: Device-specific correlations for one area
    - RoomProfile: Device-independent scanner-pair deltas for one room
    - confidence: Pure functions for z-score to confidence conversion
    - CorrelationStore: Home Assistant persistence
    - AutoLearningStats: Diagnostic statistics for auto-learning (debug tool)

"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .area_profile import AreaProfile
from .confidence import weighted_z_scores_to_confidence, z_scores_to_confidence
from .room_profile import RoomProfile
from .scanner_absolute import ScannerAbsoluteRssi
from .scanner_pair import ScannerPairCorrelation
from .store import CorrelationStore


@dataclass
class AutoLearningStats:
    """
    Statistics for auto-learning diagnostics.

    Tracks update patterns to help diagnose auto-learning behavior.
    This is a debug tool - stats reset on HA restart (not persisted).

    Attributes
    ----------
        updates_performed: Number of successful updates (new data accepted)
        updates_skipped_interval: Updates skipped due to minimum interval enforcement
        updates_skipped_confidence: Updates skipped due to low confidence (Feature 3)
        updates_skipped_dwell_time: Updates skipped due to low dwell time (Feature 5)
        updates_skipped_velocity: Updates skipped due to high velocity (Feature 5)
        updates_skipped_rssi_variance: Updates skipped due to high RSSI variance (Feature 5)
        last_update_stamp: Timestamp of last successful update

    """

    updates_performed: int = 0
    updates_skipped_interval: int = 0
    # Feature 3: Confidence filter stats
    updates_skipped_confidence: int = 0
    # Feature 5: Quality filter stats
    updates_skipped_dwell_time: int = 0
    updates_skipped_velocity: int = 0
    updates_skipped_rssi_variance: int = 0
    last_update_stamp: float = 0.0
    # Per-device stats tracking
    _device_stats: dict[str, dict[str, int]] = field(default_factory=dict, repr=False)

    def record_update(
        self,
        *,
        performed: bool,
        stamp: float,
        device_address: str | None = None,
        skip_reason: str | None = None,
    ) -> None:
        """
        Record an update attempt.

        Args:
        ----
            performed: True if update was performed, False if skipped
            stamp: Current timestamp
            device_address: Optional device address for per-device tracking
            skip_reason: Why the update was skipped (for detailed statistics).
                        Valid values: 'interval', 'low_confidence', 'low_dwell_time',
                        'high_velocity', 'high_rssi_variance'

        """
        if performed:
            self.updates_performed += 1
            self.last_update_stamp = stamp
        # Track skip reason for detailed diagnostics
        elif skip_reason == "low_confidence":
            self.updates_skipped_confidence += 1
        elif skip_reason == "low_dwell_time":
            self.updates_skipped_dwell_time += 1
        elif skip_reason == "high_velocity":
            self.updates_skipped_velocity += 1
        elif skip_reason == "high_rssi_variance":
            self.updates_skipped_rssi_variance += 1
        else:
            # Default: interval or unspecified
            self.updates_skipped_interval += 1

        # Per-device tracking
        if device_address is not None:
            if device_address not in self._device_stats:
                self._device_stats[device_address] = {"performed": 0, "skipped": 0}
            if performed:
                self._device_stats[device_address]["performed"] += 1
            else:
                self._device_stats[device_address]["skipped"] += 1

    @property
    def total_skipped(self) -> int:
        """Return total number of skipped updates across all reasons."""
        return (
            self.updates_skipped_interval
            + self.updates_skipped_confidence
            + self.updates_skipped_dwell_time
            + self.updates_skipped_velocity
            + self.updates_skipped_rssi_variance
        )

    @property
    def skip_ratio(self) -> float:
        """
        Calculate the ratio of skipped updates to total attempts.

        Returns
        -------
            Float between 0.0 and 1.0, or 0.0 if no updates recorded.

        """
        total = self.updates_performed + self.total_skipped
        if total == 0:
            return 0.0
        return self.total_skipped / total

    @property
    def total_attempts(self) -> int:
        """Return total number of update attempts."""
        return self.updates_performed + self.total_skipped

    def get_device_stats(self, device_address: str) -> dict[str, int]:
        """
        Get stats for a specific device.

        Args:
        ----
            device_address: Device MAC address

        Returns:
        -------
            Dict with 'performed' and 'skipped' counts, or zeros if not tracked.

        """
        return self._device_stats.get(device_address, {"performed": 0, "skipped": 0})

    def reset(self) -> None:
        """Reset all statistics to zero."""
        self.updates_performed = 0
        self.updates_skipped_interval = 0
        self.updates_skipped_confidence = 0
        self.updates_skipped_dwell_time = 0
        self.updates_skipped_velocity = 0
        self.updates_skipped_rssi_variance = 0
        self.last_update_stamp = 0.0
        self._device_stats.clear()

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize statistics for diagnostics output.

        Returns
        -------
            Dictionary suitable for JSON serialization.

        """
        return {
            "updates_performed": self.updates_performed,
            "updates_skipped": {
                "total": self.total_skipped,
                "interval": self.updates_skipped_interval,
                "low_confidence": self.updates_skipped_confidence,
                "low_dwell_time": self.updates_skipped_dwell_time,
                "high_velocity": self.updates_skipped_velocity,
                "high_rssi_variance": self.updates_skipped_rssi_variance,
            },
            "total_attempts": self.total_attempts,
            "skip_ratio": f"{self.skip_ratio:.1%}",
            "skip_ratio_raw": round(self.skip_ratio, 3),
            "last_update_stamp": self.last_update_stamp,
            "devices_tracked": len(self._device_stats),
        }


__all__ = [
    "AreaProfile",
    "AutoLearningStats",
    "CorrelationStore",
    "RoomProfile",
    "ScannerAbsoluteRssi",
    "ScannerPairCorrelation",
    "weighted_z_scores_to_confidence",
    "z_scores_to_confidence",
]
