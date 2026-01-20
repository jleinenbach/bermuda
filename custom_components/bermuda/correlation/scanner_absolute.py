"""
Absolute RSSI tracking for individual scanners.

Tracks the expected RSSI value from a specific scanner when a device
is confirmed in an area. Unlike the delta-based ScannerPairCorrelation,
this tracks absolute values which can be used even when the primary
scanner goes offline.

This enables "room fingerprinting" - even without the primary scanner,
we can verify if secondary scanner readings match the learned pattern.

Weighted Learning System (Two-Pool Fusion):
    - Two parallel Kalman filters: one for automatic learning, one for button training
    - Auto pool (weight 1/3): Continuously adapts to environment changes
    - Button pool (weight 2/3): Preserves manual room corrections
    - Final estimate = weighted fusion of both pools
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Self

from custom_components.bermuda.filters.kalman import KalmanFilter

from .scanner_pair import BUTTON_WEIGHT_MULTIPLIER

# Kalman parameters for absolute RSSI tracking.
# RSSI can vary more than deltas due to device orientation, battery state, etc.
RSSI_PROCESS_NOISE: float = 1.0

# Environmental and device variation.
RSSI_MEASUREMENT_NOISE: float = 25.0

# Need fewer samples than delta correlation since absolute values are noisier
# but still useful for fallback validation.
MIN_SAMPLES_FOR_MATURITY: int = 20


@dataclass(slots=True)
class ScannerAbsoluteRssi:
    """
    Tracks expected absolute RSSI from a scanner in an area.

    When a device is confirmed in an area, we track the absolute RSSI
    values seen from each scanner (not deltas). This allows us to
    verify if the device is still in the same area even when the
    primary scanner goes offline.

    Uses two parallel Kalman filters with weighted fusion:
    - Auto filter: Continuously learns from automatic room detection
    - Button filter: Learns from manual button training (weighted 2x)

    Example:
        When device is in "Büro":
        - Scanner 1 (Büro): typically -45dB (primary, might go offline)
        - Scanner 5: typically -85dB
        - Scanner 6: typically -78dB

        If Scanner 1 goes offline but Scanner 5 still shows -85dB and
        Scanner 6 still shows -78dB, the device is likely still in Büro.

    Attributes:
        scanner_address: MAC address of the scanner being tracked.

    """

    scanner_address: str
    # Two parallel Kalman filters for weighted fusion
    _kalman_auto: KalmanFilter = field(
        default_factory=lambda: KalmanFilter(
            process_noise=RSSI_PROCESS_NOISE,
            measurement_noise=RSSI_MEASUREMENT_NOISE,
        ),
        repr=False,
    )
    _kalman_button: KalmanFilter = field(
        default_factory=lambda: KalmanFilter(
            process_noise=RSSI_PROCESS_NOISE,
            measurement_noise=RSSI_MEASUREMENT_NOISE,
        ),
        repr=False,
    )

    def update(self, rssi: float) -> float:
        """
        Update with new observed absolute RSSI from automatic learning.

        Args:
            rssi: Current absolute RSSI value from this scanner.

        Returns:
            Updated fused estimate of expected RSSI.

        """
        self._kalman_auto.update(rssi)
        return self.expected_rssi

    def update_button(self, rssi: float) -> float:
        """
        Update with button-trained RSSI value.

        Button samples are weighted 2x in the final estimate fusion.

        Args:
            rssi: Current absolute RSSI value from this scanner.

        Returns:
            Updated fused estimate of expected RSSI.

        """
        self._kalman_button.update(rssi)
        return self.expected_rssi

    @property
    def expected_rssi(self) -> float:
        """
        Return weighted fusion of auto and button estimates.

        Combines both Kalman filter estimates using sample-count weighting,
        with button samples counting 2x (BUTTON_WEIGHT_MULTIPLIER).
        """
        auto_samples = self._kalman_auto.sample_count
        button_samples = self._kalman_button.sample_count

        if auto_samples == 0 and button_samples == 0:
            return 0.0
        if button_samples == 0:
            return self._kalman_auto.estimate
        if auto_samples == 0:
            return self._kalman_button.estimate

        auto_weight = float(auto_samples)
        button_weight = float(button_samples * BUTTON_WEIGHT_MULTIPLIER)
        total_weight = auto_weight + button_weight

        return (self._kalman_auto.estimate * auto_weight + self._kalman_button.estimate * button_weight) / total_weight

    @property
    def variance(self) -> float:
        """Return combined variance from both filters."""
        auto_var = self._kalman_auto.variance
        button_var = self._kalman_button.variance

        if self._kalman_auto.sample_count == 0:
            return button_var
        if self._kalman_button.sample_count == 0:
            return auto_var

        if auto_var <= 0 or button_var <= 0:
            return max(auto_var, button_var)

        return 1.0 / (1.0 / auto_var + 1.0 / button_var)

    @property
    def std_dev(self) -> float:
        """Return standard deviation of the estimate."""
        return float(self.variance**0.5)

    @property
    def auto_sample_count(self) -> int:
        """Return number of automatic learning samples."""
        return self._kalman_auto.sample_count

    @property
    def button_sample_count(self) -> int:
        """Return number of button training samples."""
        return self._kalman_button.sample_count

    @property
    def sample_count(self) -> int:
        """Return total effective sample count (weighted)."""
        return self.auto_sample_count + self.button_sample_count * BUTTON_WEIGHT_MULTIPLIER

    @property
    def is_mature(self) -> bool:
        """
        Check if profile has enough data to be trusted.

        Returns:
            True if sample_count >= MIN_SAMPLES_FOR_MATURITY.

        """
        return self.sample_count >= MIN_SAMPLES_FOR_MATURITY

    def z_score(self, observed_rssi: float) -> float:
        """
        Calculate deviation from expected value in standard deviations.

        Args:
            observed_rssi: Currently observed RSSI to compare.

        Returns:
            Absolute z-score. Lower values indicate better match.
            Returns 0.0 if variance is zero (prevents division by zero).

        """
        if self.variance <= 0:
            return 0.0
        return abs(observed_rssi - self.expected_rssi) / self.std_dev

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for persistent storage."""
        return {
            "scanner": self.scanner_address,
            # Auto filter state
            "auto_estimate": self._kalman_auto.estimate,
            "auto_variance": self._kalman_auto.variance,
            "auto_samples": self._kalman_auto.sample_count,
            # Button filter state
            "button_estimate": self._kalman_button.estimate,
            "button_variance": self._kalman_button.variance,
            "button_samples": self._kalman_button.sample_count,
            # Legacy fields for backward compatibility
            "estimate": self.expected_rssi,
            "variance": self.variance,
            "samples": self.sample_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """
        Deserialize from dictionary.

        Handles both old format (single Kalman) and new format (dual Kalman).
        """
        profile = cls(scanner_address=data["scanner"])

        if "auto_estimate" in data:
            # New format
            profile._kalman_auto.estimate = data["auto_estimate"]
            profile._kalman_auto.variance = data["auto_variance"]
            profile._kalman_auto.sample_count = data["auto_samples"]
            profile._kalman_auto._initialized = data["auto_samples"] > 0  # noqa: SLF001

            profile._kalman_button.estimate = data["button_estimate"]
            profile._kalman_button.variance = data["button_variance"]
            profile._kalman_button.sample_count = data["button_samples"]
            profile._kalman_button._initialized = data["button_samples"] > 0  # noqa: SLF001
        else:
            # Old format: migrate to auto filter
            profile._kalman_auto.estimate = data["estimate"]
            profile._kalman_auto.variance = data["variance"]
            profile._kalman_auto.sample_count = data["samples"]
            profile._kalman_auto._initialized = data["samples"] > 0  # noqa: SLF001

        return profile
