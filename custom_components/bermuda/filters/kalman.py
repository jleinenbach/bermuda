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

import logging
from dataclasses import dataclass, field
from typing import Any

from .base import FilterConfig, SignalFilter
from .const import (
    ADAPTIVE_MIN_NOISE_MULTIPLIER,
    ADAPTIVE_NOISE_SCALE_PER_10DB,
    ADAPTIVE_RSSI_OFFSET_FROM_REF,
    DEFAULT_UPDATE_DT,
    KALMAN_MEASUREMENT_NOISE,
    KALMAN_PROCESS_NOISE,
    MAX_UPDATE_DT,
    MIN_UPDATE_DT,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class KalmanFilter(SignalFilter):
    """
    1D Kalman filter for RSSI signal smoothing.

    This is a simplified Kalman filter for scalar (1D) measurements,
    optimized for BLE RSSI processing where we're tracking a single
    signal strength value over time.

    State model: x(k) = x(k-1) + w, where w ~ N(0, R)
    Observation model: z(k) = x(k) + v, where v ~ N(0, Q)

    Attributes
    ----------
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

    # Time-aware filtering: track last timestamp for dt calculation
    _last_timestamp: float | None = field(default=None, repr=False)

    # Profile Age Tracking: when first and last samples were received
    # Used for diagnostic purposes (stale profile detection, training age)
    first_sample_stamp: float | None = field(default=None, repr=False)
    last_sample_stamp: float | None = field(default=None, repr=False)

    def update(self, measurement: float, timestamp: float | None = None) -> float:
        """
        Process a new RSSI measurement using Kalman filter equations.

        The Kalman filter update consists of two steps:
        1. Predict: Project state and covariance ahead (with dt-scaled process noise)
        2. Update: Incorporate new measurement

        Time-Aware Filtering:
            When timestamps are provided, the process noise is scaled by the time
            delta (dt) since the last measurement. This is mathematically more
            correct for irregular BLE advertisement intervals:
            - Longer gaps = more uncertainty = more trust in new measurements
            - Scanner outages properly increase state uncertainty

            Formula: P_predicted = P + Q * dt (instead of P + Q)

        Args:
        ----
            measurement: Raw RSSI value in dBm
            timestamp: Optional timestamp (seconds). When provided, enables
                       time-aware filtering with dt-scaled process noise.
                       When None, uses DEFAULT_UPDATE_DT (1.0s).

        Returns:
        -------
            Filtered RSSI estimate

        """
        self.sample_count += 1

        # Profile Age Tracking: record first and last sample timestamps
        if timestamp is not None:
            if self.first_sample_stamp is None:
                self.first_sample_stamp = timestamp
            self.last_sample_stamp = timestamp

        # Calculate dt for time-aware process noise scaling
        dt = DEFAULT_UPDATE_DT
        if timestamp is not None:
            if self._last_timestamp is not None:
                # Clamp dt to reasonable bounds
                raw_dt = timestamp - self._last_timestamp
                dt = max(MIN_UPDATE_DT, min(raw_dt, MAX_UPDATE_DT))
            self._last_timestamp = timestamp

        if not self._initialized:
            # First measurement - initialize state
            self.estimate = measurement
            self.variance = self.measurement_noise
            self._initialized = True
            if timestamp is not None:
                self._last_timestamp = timestamp
            return self.estimate

        # Predict step
        # For static model: predicted state = current state
        # Predicted variance increases by process noise SCALED BY dt
        # This models: longer time = more uncertainty about current state
        predicted_variance = self.variance + self.process_noise * dt

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
        ----
            measurement: New RSSI measurement in dBm
            ref_power: Device's calibrated RSSI at 1m (from beacon_power or config)
            timestamp: Optional (unused, but part of interface)

        Returns:
        -------
            Filtered RSSI estimate in dBm

        References:
        ----------
            - "Variational Bayesian Adaptive UKF for RSSI-based Indoor Localization"
            - PMC5461075: "An Improved BLE Indoor Localization with Kalman-Based Fusion"

        """
        # Validate ref_power (calibrated RSSI at 1m, NOT TX power!)
        # Valid range: -100 to 0 dBm (always negative for RSSI measurements)
        # Note: BLE TX power (transmit strength) can be positive (+3 dBm for ESP32),
        # but ref_power represents the received signal strength at 1 meter, which is
        # always negative. If you see positive values here, the device is likely
        # reporting TX power instead of calibrated RSSI - check beacon_power or
        # device configuration.
        # Invalid values cause incorrect adaptive noise scaling.
        if not (-100 <= ref_power <= 0):
            _LOGGER.warning(
                "Invalid ref_power %.1f dBm (expected -100 to 0). "
                "ref_power should be calibrated RSSI at 1m (always negative), not TX power. "
                "Check device's beacon_power or ref_power configuration. Using default -55",
                ref_power,
            )
            ref_power = -55.0  # Safe default for most BLE devices

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
        self._last_timestamp = None
        self.first_sample_stamp = None
        self.last_sample_stamp = None

    def reset_to_value(
        self,
        value: float,
        variance: float = 0.01,
        sample_count: int = 500,
    ) -> None:
        """
        Force filter to a specific state (Teacher Forcing).

        Used when an authoritative external source (User) provides ground truth.
        This bypasses normal Kalman filter dynamics and directly sets the state.

        The default parameters (variance=0.01, sample_count=500) create a
        "frozen" state with extremely high confidence that won't be easily
        overwritten by future automatic updates.

        Args:
        ----
            value: The authoritative value to set as the estimate.
            variance: The variance to assign (lower = more confident).
                      Default 0.01 = extremely high confidence.
            sample_count: The sample count to assign.
                         Default 500 = massive inertia against drift.

        """
        self.estimate = value
        self.variance = variance
        self.sample_count = sample_count
        self._initialized = True

    def reset_variance_only(self, target_variance: float | None = None) -> None:
        """
        Reset variance while preserving the estimate (for multi-position training).

        This method is used when starting a new training session for a device
        that already has training data. By resetting variance but keeping the
        estimate, we allow new samples to have equal influence to previous
        training sessions.

        Without this, subsequent training sessions would have diminishing
        influence due to the already-low variance from previous training.

        Args:
        ----
            target_variance: Variance to reset to. If None, uses measurement_noise.
                            Higher values = more trust in new measurements.

        Example:
        -------
            Session 1: Train at position A → estimate=-75dB, variance=3
            Session 2: Without reset → new samples have ~10% influence (bad!)
            Session 2: With reset → variance=25, new samples have ~50% influence (good!)

        """
        if not self._initialized:
            return  # Nothing to reset if filter hasn't been used

        self.variance = target_variance if target_variance is not None else self.measurement_noise
        # Reset timestamp to avoid dt-scaling issues with large time gaps
        self._last_timestamp = None
        # Note: estimate and sample_count are preserved!

    def restore_state(
        self,
        estimate: float,
        variance: float,
        sample_count: int,
    ) -> None:
        """
        Restore filter state from serialized data (deserialization).

        Unlike reset_to_value() which creates an authoritative "frozen" state,
        this method restores a previously saved filter state exactly as it was.
        Used when loading correlation data from persistent storage.

        This method encapsulates the internal state management, avoiding direct
        access to _initialized from external code (see CLAUDE.md BUG 6 / Clean Code).

        Args:
        ----
            estimate: Previously saved estimate value.
            variance: Previously saved variance value.
            sample_count: Previously saved sample count.

        Note:
        ----
            The filter is marked as initialized if sample_count > 0.
            This matches the semantics: a filter with samples has data.

        """
        self.estimate = estimate
        self.variance = variance
        self.sample_count = sample_count
        self._initialized = sample_count > 0

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
            "first_sample_stamp": self.first_sample_stamp,
            "last_sample_stamp": self.last_sample_stamp,
        }

    @classmethod
    def from_config(cls, config: FilterConfig) -> KalmanFilter:
        """Create a KalmanFilter from a FilterConfig."""
        return cls(
            process_noise=config.process_noise,
            measurement_noise=config.measurement_noise,
            variance=config.initial_variance,
        )

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize filter state for persistence.

        Returns:
        -------
            Dictionary containing all state needed to restore the filter.
            Can be passed to from_dict() for deserialization.

        Example:
        -------
            >>> kf = KalmanFilter()
            >>> kf.update(-70.0)
            >>> state = kf.to_dict()
            >>> # Later...
            >>> kf_restored = KalmanFilter.from_dict(state)

        """
        return {
            "estimate": self.estimate,
            "variance": self.variance,
            "sample_count": self.sample_count,
            "process_noise": self.process_noise,
            "measurement_noise": self.measurement_noise,
            "last_timestamp": self._last_timestamp,
            "first_sample_stamp": self.first_sample_stamp,
            "last_sample_stamp": self.last_sample_stamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KalmanFilter:
        """
        Deserialize filter from dictionary.

        Args:
        ----
            data: Dictionary from to_dict() containing filter state.

        Returns:
        -------
            Restored KalmanFilter with previous state.

        Raises:
        ------
            KeyError: If required fields are missing from data.

        """
        filter_instance = cls(
            process_noise=data.get("process_noise", KALMAN_PROCESS_NOISE),
            measurement_noise=data.get("measurement_noise", KALMAN_MEASUREMENT_NOISE),
        )
        filter_instance.restore_state(
            estimate=data.get("estimate", 0.0),
            variance=data.get("variance", KALMAN_MEASUREMENT_NOISE),
            sample_count=data.get("sample_count", 0),
        )
        # Restore timestamp for time-aware filtering
        filter_instance._last_timestamp = data.get("last_timestamp")
        # Restore profile age timestamps
        filter_instance.first_sample_stamp = data.get("first_sample_stamp")
        filter_instance.last_sample_stamp = data.get("last_sample_stamp")
        return filter_instance
