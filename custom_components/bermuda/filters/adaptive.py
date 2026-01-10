"""
Adaptive statistics and changepoint detection for BLE RSSI filtering.

This module provides:
- AdaptiveStatistics: Online mean/variance estimation with CUSUM changepoint detection
- AdaptiveRobustFilter: Full SignalFilter implementation wrapping AdaptiveStatistics

The design follows industrial statistical process control standards,
adapted for the specific characteristics of BLE RSSI signals.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .base import FilterConfig, SignalFilter
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

    def to_dict(self) -> dict[str, Any]:
        """Export statistics as dictionary for diagnostics."""
        return {
            "mean": round(self.mean, 1),
            "stddev": round(self.stddev, 2),
            "sample_count": self.sample_count,
            "changepoints": self.last_changepoint,
        }


@dataclass
class AdaptiveRobustFilter(SignalFilter):
    """
    Robust adaptive filter implementing the SignalFilter interface.

    This filter combines EMA-based estimation with CUSUM changepoint
    detection. It's designed for scenarios where:
    - The signal may have occasional outliers
    - The underlying signal characteristics may change over time
    - Detecting these changes is important

    The filter wraps AdaptiveStatistics and provides the standard
    SignalFilter interface for interoperability with other filters.
    """

    # Internal statistics engine
    _stats: AdaptiveStatistics = field(default_factory=AdaptiveStatistics)

    # Configuration
    alpha: float = EMA_ALPHA_SLOW

    # Track last changepoint detection result
    _last_changepoint_detected: bool = False

    def __post_init__(self) -> None:
        """Initialize internal stats with configured alpha."""
        self._stats.alpha = self.alpha

    def update(self, measurement: float, timestamp: float | None = None) -> float:
        """
        Process a new measurement and update filter state.

        Args:
            measurement: Raw RSSI value in dBm
            timestamp: Optional (unused, part of interface)

        Returns:
            The filtered estimate (EMA mean)

        """
        self._last_changepoint_detected = self._stats.update(measurement)
        return self._stats.mean

    def get_estimate(self) -> float:
        """Return current EMA mean estimate."""
        return self._stats.mean

    def get_variance(self) -> float:
        """Return current EMA variance estimate."""
        return self._stats.variance

    def reset(self) -> None:
        """Reset filter to initial state."""
        self._stats.reset()
        self._last_changepoint_detected = False

    def get_diagnostics(self) -> dict[str, Any]:
        """Return diagnostic information including CUSUM state."""
        diag = self._stats.to_dict()
        diag["cusum_pos"] = round(self._stats.cusum_pos, 2)
        diag["cusum_neg"] = round(self._stats.cusum_neg, 2)
        diag["changepoint_detected"] = self._last_changepoint_detected
        return diag

    def changepoint_detected(self) -> bool:
        """Return True if last update detected a changepoint."""
        return self._last_changepoint_detected

    @classmethod
    def from_config(cls, config: FilterConfig) -> AdaptiveRobustFilter:
        """Create filter from configuration."""
        return cls(alpha=config.ema_alpha)
