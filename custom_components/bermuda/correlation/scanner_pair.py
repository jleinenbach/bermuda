"""
Single scanner-pair correlation tracking.

Wraps a Kalman filter to track the typical RSSI delta between
a primary scanner and another scanner when a device is in a specific area.

This module is part of the scanner correlation learning system that
improves area localization by learning spatial relationships between scanners.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Self

from custom_components.bermuda.filters.kalman import KalmanFilter

# Kalman parameters tuned for RSSI delta tracking.
# Deltas are fairly stable (rooms don't move), so low process noise.
DELTA_PROCESS_NOISE: float = 0.5

# Environmental variation (people moving, doors opening) adds measurement noise.
DELTA_MEASUREMENT_NOISE: float = 16.0

# Statistical confidence threshold before trusting the learned correlation.
MIN_SAMPLES_FOR_MATURITY: int = 30


@dataclass(slots=True)
class ScannerPairCorrelation:
    """
    Tracks learned RSSI delta from primary scanner to another scanner.

    The delta is defined as: primary_rssi - other_rssi
    Positive delta means the other scanner sees a weaker signal.

    Uses Kalman filtering to maintain a running estimate that:
    - Smooths out noise from multipath/interference
    - Adapts to gradual environmental changes
    - Provides uncertainty quantification via variance

    Attributes:
        scanner_address: MAC address of the "other" scanner being correlated.

    """

    scanner_address: str
    _kalman: KalmanFilter = field(
        default_factory=lambda: KalmanFilter(
            process_noise=DELTA_PROCESS_NOISE,
            measurement_noise=DELTA_MEASUREMENT_NOISE,
        ),
        repr=False,
    )

    def update(self, observed_delta: float) -> float:
        """
        Update correlation with new observed delta.

        Args:
            observed_delta: Current (primary_rssi - other_rssi) value.

        Returns:
            Updated Kalman estimate of the expected delta.

        """
        return self._kalman.update(observed_delta)

    @property
    def expected_delta(self) -> float:
        """Return learned expected delta (primary_rssi - other_rssi)."""
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
        """Return number of samples processed by the Kalman filter."""
        return self._kalman.sample_count

    @property
    def is_mature(self) -> bool:
        """
        Check if correlation has enough data to be trusted.

        Immature correlations should not affect area selection decisions
        as their estimates are statistically unreliable.

        Returns:
            True if sample_count >= MIN_SAMPLES_FOR_MATURITY.

        """
        return self.sample_count >= MIN_SAMPLES_FOR_MATURITY

    def z_score(self, observed_delta: float) -> float:
        """
        Calculate deviation from expectation in standard deviations.

        Args:
            observed_delta: Currently observed delta to compare.

        Returns:
            Absolute z-score. Lower values indicate better match.
            Returns 0.0 if variance is zero (prevents division by zero).

        """
        if self.variance <= 0:
            return 0.0
        return abs(observed_delta - self.expected_delta) / self.std_dev

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize to dictionary for persistent storage.

        Returns:
            Dictionary with scanner address and Kalman state.

        """
        return {
            "scanner": self.scanner_address,
            "estimate": self._kalman.estimate,
            "variance": self._kalman.variance,
            "samples": self._kalman.sample_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """
        Deserialize from dictionary.

        Args:
            data: Dictionary from to_dict().

        Returns:
            Restored ScannerPairCorrelation instance.

        """
        corr = cls(scanner_address=data["scanner"])
        corr._kalman.estimate = data["estimate"]
        corr._kalman.variance = data["variance"]
        corr._kalman.sample_count = data["samples"]
        corr._kalman._initialized = data["samples"] > 0  # noqa: SLF001
        return corr
