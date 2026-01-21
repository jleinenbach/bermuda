"""
Absolute RSSI tracking for individual scanners.

Tracks the expected RSSI value from a specific scanner when a device
is confirmed in an area. Unlike the delta-based ScannerPairCorrelation,
this tracks absolute values which can be used even when the primary
scanner goes offline.

This enables "room fingerprinting" - even without the primary scanner,
we can verify if secondary scanner readings match the learned pattern.

Hierarchical Priority System (Frozen Layers & Shadow Learning):
    - Two parallel Kalman filters: one for automatic learning, one for button training
    - Button training ALWAYS overrides auto-learning (no mixing/fusion)
    - Once a user trains a room, that value is "frozen" - auto-learning cannot change it
    - Auto-learning continues in "shadow mode" (for diagnostics) but doesn't affect output
    - This ensures user calibration persists indefinitely against environment drift
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Self

from custom_components.bermuda.filters.kalman import KalmanFilter

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

    Uses HIERARCHICAL PRIORITY (not fusion):
    - Auto filter: Continuously learns from automatic room detection (Shadow Learning)
    - Button filter: Learns from manual button training (Frozen Layer)

    Priority Logic:
    - If button filter is initialized → use ONLY button value (user truth is absolute)
    - If button filter is not initialized → fallback to auto value
    - Auto-learning continues but has NO INFLUENCE when button data exists

    This ensures that once a user trains a room, the calibration persists
    indefinitely regardless of how much the auto-learning drifts over time.

    Example:
        User trains device in "Keller" (cellar):
        - Button filter learns: Scanner Wohnzimmer = -85dB
        - Auto filter continues learning in shadow: -90dB, -80dB, etc.
        - expected_rssi always returns -85dB (button value)
        - Even after months of auto-drift, the room stays stable

    Attributes:
        scanner_address: MAC address of the scanner being tracked.

    """

    scanner_address: str
    # Two parallel Kalman filters for hierarchical priority
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
        Update with button-trained RSSI value (The Frozen Layer).

        This creates a "frozen" state that completely overrides auto-learning.
        The button filter is set to extremely high confidence (variance=0.01)
        and high sample count (500) to ensure it dominates any future operations.

        IMPORTANT: Once this is called, the auto-filter becomes "shadow only" -
        it continues learning but has NO INFLUENCE on expected_rssi.

        Args:
            rssi: Current absolute RSSI value from this scanner.

        Returns:
            The frozen button estimate (user truth).

        """
        # Use reset_to_value to create a frozen, high-confidence state
        # - variance=0.01: Extremely high confidence
        # - sample_count=500: Massive inertia (would need ~500 contrary samples to shift)
        self._kalman_button.reset_to_value(
            value=rssi,
            variance=0.01,
            sample_count=500,
        )
        return self.expected_rssi

    @property
    def expected_rssi(self) -> float:
        """
        Return expected RSSI using HIERARCHICAL PRIORITY (not fusion).

        Priority Logic:
        1. If button filter is initialized → return button estimate (user truth is absolute)
        2. If button filter is not initialized → return auto estimate (learned fallback)
        3. If neither is initialized → return 0.0

        This ensures user training ALWAYS overrides auto-learning, regardless
        of how many auto-samples have been collected or their variance.
        """
        # PRIORITY 1: User training is absolute truth
        if self._kalman_button.is_initialized:
            return self._kalman_button.estimate

        # PRIORITY 2: Fallback to auto-learning if no user training
        if self._kalman_auto.is_initialized:
            return self._kalman_auto.estimate

        # No data available
        return 0.0

    @property
    def variance(self) -> float:
        """
        Return variance of the ACTIVE filter (hierarchical priority).

        Priority Logic:
        1. If button filter is initialized → return button variance only
        2. If button filter is not initialized → return auto variance

        We do NOT combine variances because that would dilute the high
        confidence of user training with the uncertainty of auto-learning.
        """
        # PRIORITY 1: User training variance (very low = high confidence)
        if self._kalman_button.is_initialized:
            return self._kalman_button.variance

        # PRIORITY 2: Fallback to auto variance
        return self._kalman_auto.variance

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
        """Return total sample count for maturity checks."""
        return self.auto_sample_count + self.button_sample_count

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
