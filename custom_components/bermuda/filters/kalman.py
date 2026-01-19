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
from .const import (
    ADAPTIVE_MIN_NOISE_MULTIPLIER,
    ADAPTIVE_NOISE_SCALE_PER_10DB,
    ADAPTIVE_RSSI_OFFSET_FROM_REF,
    KALMAN_MEASUREMENT_NOISE,
    KALMAN_PROCESS_NOISE,
)


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

    def update_adaptive(
        self,
        measurement: float,
        ref_power: float,
        timestamp: float | None = None,
    ) -> float:
        """
        Process measurement with RSSI-adaptive measurement noise.

        Scientific basis: RSSI measurement variance increases with distance.
        Research shows SNR degrades as distance increases, meaning weaker
        signals have higher noise variance and should be trusted less.

        The measurement noise is scaled based on signal strength RELATIVE to
        the device's ref_power (calibrated RSSI at 1m). This ensures the
        adaptive behavior works correctly regardless of device TX power.

        Formula: R_adaptive = R_base * scale^((threshold - rssi) / 10)
        Where: threshold = ref_power - ADAPTIVE_RSSI_OFFSET_FROM_REF

        Example with ref_power = -55 dBm (typical), offset = 10 dB:
        - threshold = -65 dBm
        - At -65 dBm (~3m): base noise
        - At -75 dBm (~6m): 1.5x noise
        - At -85 dBm (~15m): 2.25x noise

        Example with ref_power = -94 dBm (ultra-low TX), offset = 10 dB:
        - threshold = -104 dBm
        - At -100 dBm (~2m): base noise * 0.7 (trusted!)
        - At -104 dBm (~3m): base noise
        - At -114 dBm (~6m): 1.5x noise

        Args:
            measurement: New RSSI measurement in dBm
            ref_power: Device's calibrated RSSI at 1m (from beacon_power or config)
            timestamp: Optional (unused, but part of interface)

        Returns:
            Filtered RSSI estimate in dBm

        References:
            - "Variational Bayesian Adaptive UKF for RSSI-based Indoor Localization"
            - PMC5461075: "An Improved BLE Indoor Localization with Kalman-Based Fusion"

        """
        # Calculate device-relative threshold
        # Signals within OFFSET dB of ref_power are considered "strong"
        threshold = ref_power - ADAPTIVE_RSSI_OFFSET_FROM_REF

        # Calculate adaptive measurement noise based on signal strength
        db_below_threshold = threshold - measurement

        if db_below_threshold > 0:
            # Weaker signal = higher noise (less trust)
            noise_multiplier = ADAPTIVE_NOISE_SCALE_PER_10DB ** (db_below_threshold / 10.0)
        else:
            # Stronger signal = lower noise, but cap at minimum
            noise_multiplier = max(
                ADAPTIVE_MIN_NOISE_MULTIPLIER,
                ADAPTIVE_NOISE_SCALE_PER_10DB ** (db_below_threshold / 10.0),
            )

        # Temporarily apply adaptive noise
        original_noise = self.measurement_noise
        self.measurement_noise = original_noise * noise_multiplier

        # Run standard Kalman update
        result = self.update(measurement, timestamp)

        # Restore original noise for next call
        self.measurement_noise = original_noise

        return result

    @property
    def is_initialized(self) -> bool:
        """Whether the filter has received at least one measurement."""
        return self._initialized

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
            "kalman_gain": round(self.variance / (self.variance + self.measurement_noise), 4)
            if self._initialized
            else 0.0,
            "initialized": self._initialized,
        }

    @classmethod
    def from_config(cls, config: FilterConfig) -> KalmanFilter:
        """Create a KalmanFilter from a FilterConfig."""
        return cls(
            process_noise=config.process_noise,
            measurement_noise=config.measurement_noise,
            variance=config.initial_variance,
        )
