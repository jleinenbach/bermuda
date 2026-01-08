"""
Kalman filter implementation for BLE RSSI signal processing.

The Kalman filter is optimal for linear systems with Gaussian noise.
While BLE RSSI has heavier tails than Gaussian, the Kalman filter
still provides good performance and is computationally efficient.

This implementation is based on research from:
- Wouter Bulten: https://www.wouterbulten.nl/posts/kalman-filters-explained-removing-noise-from-rssi-signals/
- PMC5461075: BLE Indoor Localization with Kalman-Based Fusion

Typical parameters for BLE RSSI:
- R (process noise): 0.008 for static positioning
- Q (measurement noise): 4.0 (derived from signal variance)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .base import FilterConfig, SignalFilter
from .const import KALMAN_MEASUREMENT_NOISE, KALMAN_PROCESS_NOISE


@dataclass
class KalmanFilter(SignalFilter):
    """
    1D Kalman filter for RSSI signal smoothing.

    This is a simplified Kalman filter for scalar (1D) measurements,
    optimized for BLE RSSI processing where we're tracking a single
    signal strength value over time.

    State model: x(k) = x(k-1) + w, where w ~ N(0, R)
    Observation model: z(k) = x(k) + v, where v ~ N(0, Q)

    Attributes:
        estimate: Current state estimate (filtered RSSI)
        variance: Current error covariance (uncertainty)
        process_noise: R - variance of state transition noise
        measurement_noise: Q - variance of measurement noise
        sample_count: Number of samples processed
    """

    # Filter state
    estimate: float = 0.0
    variance: float = field(default_factory=lambda: KALMAN_MEASUREMENT_NOISE)
    sample_count: int = 0

    # Filter parameters (can be tuned)
    process_noise: float = KALMAN_PROCESS_NOISE  # R
    measurement_noise: float = KALMAN_MEASUREMENT_NOISE  # Q

    # Track if filter is initialized
    _initialized: bool = False

    def update(self, measurement: float, timestamp: float | None = None) -> float:
        """
        Process a new RSSI measurement using Kalman filter equations.

        The Kalman filter update consists of two steps:
        1. Predict: Project state and covariance ahead
        2. Update: Incorporate new measurement

        Args:
            measurement: Raw RSSI value in dBm
            timestamp: Optional (unused in basic Kalman, but part of interface)

        Returns:
            Filtered RSSI estimate
        """
        self.sample_count += 1

        if not self._initialized:
            # First measurement - initialize state
            self.estimate = measurement
            self.variance = self.measurement_noise
            self._initialized = True
            return self.estimate

        # Predict step
        # For static model: predicted state = current state
        # Predicted variance increases by process noise
        predicted_variance = self.variance + self.process_noise

        # Update step
        # Kalman gain: K = P / (P + Q)
        kalman_gain = predicted_variance / (predicted_variance + self.measurement_noise)

        # Updated estimate: x = x + K * (z - x)
        innovation = measurement - self.estimate
        self.estimate = self.estimate + kalman_gain * innovation

        # Updated variance: P = (1 - K) * P
        self.variance = (1 - kalman_gain) * predicted_variance

        return self.estimate

    def get_estimate(self) -> float:
        """Return current filtered RSSI estimate."""
        return self.estimate

    def get_variance(self) -> float:
        """Return current error covariance."""
        return self.variance

    def reset(self) -> None:
        """Reset filter to initial state."""
        self.estimate = 0.0
        self.variance = self.measurement_noise
        self.sample_count = 0
        self._initialized = False

    def get_diagnostics(self) -> dict[str, Any]:
        """Return diagnostic information including Kalman-specific state."""
        return {
            "estimate": round(self.estimate, 2),
            "variance": round(self.variance, 4),
            "std_dev": round(self.variance**0.5, 2),
            "sample_count": self.sample_count,
            "kalman_gain": round(
                self.variance / (self.variance + self.measurement_noise), 4
            )
            if self._initialized
            else 0.0,
            "initialized": self._initialized,
        }

    @classmethod
    def from_config(cls, config: FilterConfig) -> "KalmanFilter":
        """Create a KalmanFilter from a FilterConfig."""
        return cls(
            process_noise=config.process_noise,
            measurement_noise=config.measurement_noise,
            variance=config.initial_variance,
        )
