"""
Adaptive statistics and changepoint detection for BLE RSSI filtering.

This module provides online estimation of statistical parameters that adapt
over time, along with CUSUM-based changepoint detection for identifying
significant shifts in signal characteristics.

The design follows industrial statistical process control standards,
adapted for the specific characteristics of BLE RSSI signals.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .const import (
    BLE_RSSI_TYPICAL_STDDEV,
    CUSUM_DRIFT_SIGMA,
    CUSUM_THRESHOLD_SIGMA,
    EMA_ALPHA_SLOW,
)


@dataclass
class AdaptiveStatistics:
    """
    Self-adapting statistics using EMA for mean and variance estimation.

    This class implements online estimation of statistical parameters that
    adapt over time using Exponential Moving Average (EMA). This is useful
    for detecting when underlying signal characteristics change without
    requiring manual calibration.

    The implementation combines:
    - Welford's online algorithm for numerical stability
    - EMA smoothing for adaptation to changing conditions
    - CUSUM changepoint detection for significant shifts

    Attributes:
        mean: Current estimated mean value
        variance: Current estimated variance
        sample_count: Total number of samples processed
        alpha: EMA smoothing factor (0 < alpha < 1)
        cusum_pos: CUSUM statistic for positive shifts
        cusum_neg: CUSUM statistic for negative shifts
        last_changepoint: Sample count at last detected changepoint
    """

    mean: float = 0.0
    variance: float = field(default_factory=lambda: BLE_RSSI_TYPICAL_STDDEV**2)
    sample_count: int = 0
    alpha: float = EMA_ALPHA_SLOW

    # CUSUM state for changepoint detection
    cusum_pos: float = 0.0
    cusum_neg: float = 0.0
    last_changepoint: int = 0

    @property
    def stddev(self) -> float:
        """Standard deviation derived from variance."""
        # Floor at 0.1 to avoid division by zero in CUSUM calculations
        return math.sqrt(max(self.variance, 0.1))

    def update(self, value: float) -> bool:
        """
        Update statistics with a new observation.

        Args:
            value: New RSSI measurement (typically negative dBm)

        Returns:
            True if a changepoint was detected (significant shift in mean),
            False otherwise.
        """
        self.sample_count += 1

        if self.sample_count == 1:
            # First sample - initialize mean directly
            self.mean = value
            return False

        # EMA update for mean
        old_mean = self.mean
        self.mean = self.alpha * value + (1 - self.alpha) * self.mean

        # EMA update for variance (using squared deviation from old mean)
        # This is a simplified online variance that trades some accuracy
        # for computational efficiency and adaptability
        deviation_sq = (value - old_mean) ** 2
        self.variance = self.alpha * deviation_sq + (1 - self.alpha) * self.variance

        # CUSUM changepoint detection
        return self._update_cusum(value)

    def _update_cusum(self, value: float) -> bool:
        """
        Update CUSUM statistics and check for changepoint.

        CUSUM (Cumulative Sum) is an industrial-standard algorithm for
        detecting shifts in the mean of a process. It accumulates deviations
        from the expected value, triggering an alarm when the accumulated
        deviation exceeds a threshold.

        The two-sided CUSUM tracks both positive shifts (cusum_pos) and
        negative shifts (cusum_neg) independently.

        Args:
            value: The current observation

        Returns:
            True if a changepoint was detected, False otherwise.
        """
        # Normalize deviation by standard deviation (z-score)
        z = (value - self.mean) / self.stddev

        # Drift term prevents false alarms in stable conditions
        drift = CUSUM_DRIFT_SIGMA

        # Update CUSUM for positive shifts (mean increasing)
        self.cusum_pos = max(0.0, self.cusum_pos + z - drift)

        # Update CUSUM for negative shifts (mean decreasing)
        self.cusum_neg = max(0.0, self.cusum_neg - z - drift)

        # Check for changepoint (either direction)
        threshold = CUSUM_THRESHOLD_SIGMA
        if self.cusum_pos > threshold or self.cusum_neg > threshold:
            # Reset CUSUM after detection to avoid repeated triggers
            self.cusum_pos = 0.0
            self.cusum_neg = 0.0
            self.last_changepoint = self.sample_count
            return True

        return False

    def reset(self) -> None:
        """Reset all statistics to initial state."""
        self.mean = 0.0
        self.variance = BLE_RSSI_TYPICAL_STDDEV**2
        self.sample_count = 0
        self.cusum_pos = 0.0
        self.cusum_neg = 0.0
        self.last_changepoint = 0

    def to_dict(self) -> dict:
        """Export statistics as dictionary for diagnostics."""
        return {
            "mean": round(self.mean, 1),
            "stddev": round(self.stddev, 2),
            "sample_count": self.sample_count,
            "changepoints": self.last_changepoint,
        }
