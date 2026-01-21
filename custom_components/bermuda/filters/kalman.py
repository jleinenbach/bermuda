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
    IAE_NIS_THRESHOLD,
    IAE_Q_DECAY,
    IAE_Q_MAX_MULTIPLIER,
    IAE_Q_SCALE,
    KALMAN_MEASUREMENT_NOISE,
    KALMAN_PROCESS_NOISE,
)


@dataclass
class KalmanFilter(SignalFilter):
    """
    1D Kalman filter with Innovation-Based Adaptive Estimation (IAE).

    This is a simplified Kalman filter for scalar (1D) measurements,
    optimized for BLE RSSI processing where we're tracking a single
    signal strength value over time.

    **Innovation-Based Adaptive Estimation (IAE):**
    Standard Kalman filters use fixed process noise (Q), creating a dilemma:
    - Low Q = smooth tracking but slow response to movement
    - High Q = fast response but jittery when stationary

    IAE solves this by monitoring the Normalized Innovation Squared (NIS):
    NIS = innovation² / S, where S is the expected innovation variance.

    When NIS > 1.0, measurements deviate more than noise alone can explain,
    indicating device movement. The filter temporarily increases Q to "wake up"
    and track the new position. When NIS falls back, Q decays toward baseline,
    providing smooth settling behavior.

    State model: x(k) = x(k-1) + w, where w ~ N(0, R)
    Observation model: z(k) = x(k) + v, where v ~ N(0, Q)

    Attributes:
        estimate: Current state estimate (filtered RSSI)
        variance: Current error covariance (uncertainty)
        process_noise: R - variance of state transition noise (adaptive via IAE)
        measurement_noise: Q - variance of measurement noise
        sample_count: Number of samples processed
        q_min: Baseline process noise for stationary devices
        q_scale: Scaling factor for Q adaptation (IAE)
        last_nis: Most recent Normalized Innovation Squared value

    """

    # Filter state
    estimate: float = 0.0
    variance: float = field(default_factory=lambda: KALMAN_MEASUREMENT_NOISE)
    sample_count: int = 0

    # Filter parameters (can be tuned)
    process_noise: float = KALMAN_PROCESS_NOISE  # R - current (adaptive)
    measurement_noise: float = KALMAN_MEASUREMENT_NOISE  # Q

    # IAE (Innovation-Based Adaptive Estimation) parameters
    q_min: float = KALMAN_PROCESS_NOISE  # Baseline process noise
    q_scale: float = IAE_Q_SCALE  # Scaling factor for NIS-based adaptation
    last_nis: float = 0.0  # Most recent Normalized Innovation Squared

    # Track if filter is initialized
    _initialized: bool = False

    def update(self, measurement: float, timestamp: float | None = None) -> float:
        """
        Process a new RSSI measurement using Kalman filter with IAE.

        The Kalman filter update consists of three steps:
        1. Predict: Project state and covariance ahead
        2. IAE: Adapt process noise based on innovation
        3. Update: Incorporate new measurement

        Innovation-Based Adaptive Estimation (IAE):
        Monitors the Normalized Innovation Squared (NIS) = innovation² / S.
        When NIS > 1.0 (measurement deviates more than noise explains),
        the device is likely moving, so Q increases for faster tracking.
        When NIS ≤ 1.0, Q decays back to baseline for smooth filtering.

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
            self.process_noise = self.q_min  # Start with baseline Q
            self._initialized = True
            return self.estimate

        # =================================================================
        # IAE Step: Compute NIS and adapt process noise BEFORE prediction
        # =================================================================
        # Innovation (pre-update): how much does measurement differ from estimate?
        innovation = measurement - self.estimate

        # Expected innovation variance (S): combines state uncertainty and measurement noise
        # This is what we'd expect the innovation variance to be if the model is correct
        innovation_variance = self.variance + self.measurement_noise

        # Normalized Innovation Squared (NIS): innovation² / S
        # NIS ~ χ²(1) under ideal conditions, so E[NIS] = 1
        # NIS >> 1 indicates model mismatch (device is maneuvering/moving)
        # NIS << 1 indicates over-smoothing or very accurate predictions
        self.last_nis = (innovation * innovation) / innovation_variance if innovation_variance > 0 else 0.0

        # Adapt process noise based on NIS
        if self.last_nis > IAE_NIS_THRESHOLD:
            # Device appears to be moving - increase Q for faster response
            # Formula: Q = Q_min * (1 + scale * (NIS - threshold))
            q_multiplier = 1.0 + self.q_scale * (self.last_nis - IAE_NIS_THRESHOLD)
            # Cap to prevent instability during extreme spikes
            q_multiplier = min(q_multiplier, IAE_Q_MAX_MULTIPLIER)
            self.process_noise = self.q_min * q_multiplier
        else:
            # Device appears stationary - decay Q toward baseline
            # Smooth decay provides natural settling behavior
            self.process_noise = self.q_min + (self.process_noise - self.q_min) * IAE_Q_DECAY

        # =================================================================
        # Predict step
        # =================================================================
        # For static model: predicted state = current state
        # Predicted variance increases by (adaptive) process noise
        predicted_variance = self.variance + self.process_noise

        # =================================================================
        # Update step
        # =================================================================
        # Kalman gain: K = P / (P + Q)
        kalman_gain = predicted_variance / (predicted_variance + self.measurement_noise)

        # Updated estimate: x = x + K * (z - x)
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
        self.process_noise = self.q_min  # Reset adaptive Q to baseline
        self.last_nis = 0.0
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
            # IAE diagnostics
            "process_noise": round(self.process_noise, 6),
            "q_min": round(self.q_min, 6),
            "q_multiplier": round(self.process_noise / self.q_min, 2) if self.q_min > 0 else 1.0,
            "last_nis": round(self.last_nis, 3),
            "iae_state": "tracking" if self.last_nis > IAE_NIS_THRESHOLD else "settled",
        }

    @classmethod
    def from_config(cls, config: FilterConfig) -> KalmanFilter:
        """Create a KalmanFilter from a FilterConfig."""
        return cls(
            process_noise=config.process_noise,
            measurement_noise=config.measurement_noise,
            variance=config.initial_variance,
            q_min=config.process_noise,  # Use configured process_noise as baseline for IAE
        )
