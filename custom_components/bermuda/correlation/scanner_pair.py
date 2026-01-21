"""
Single scanner-pair correlation tracking.

Wraps Kalman filters to track the typical RSSI delta between
a primary scanner and another scanner when a device is in a specific area.

This module is part of the scanner correlation learning system that
improves area localization by learning spatial relationships between scanners.

Hierarchical Priority System (Frozen Layers & Shadow Learning):
    - Two parallel Kalman filters: one for automatic learning, one for button training
    - Button training ALWAYS overrides auto-learning (no mixing/fusion)
    - Once a user trains a room, that value is "frozen" - auto-learning cannot change it
    - Auto-learning continues in "shadow mode" (for diagnostics) but doesn't affect output
    - This ensures user calibration persists indefinitely against environment drift

Priority Logic:
    - If button filter is initialized → use ONLY button value
    - If button filter is not initialized → fallback to auto value
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

# Minimum variance to prevent division by zero and numerical instability
MIN_VARIANCE: float = 0.001


@dataclass(slots=True)
class ScannerPairCorrelation:
    """
    Tracks learned RSSI delta from primary scanner to another scanner.

    The delta is defined as: primary_rssi - other_rssi
    Positive delta means the other scanner sees a weaker signal.

    Uses HIERARCHICAL PRIORITY (not fusion):
    - Auto filter: Continuously learns from automatic room detection (Shadow Learning)
    - Button filter: Learns from manual button training (Frozen Layer)

    Priority Logic:
    - If button filter is initialized → use ONLY button value (user truth is absolute)
    - If button filter is not initialized → fallback to auto value
    - Auto-learning continues but has NO INFLUENCE when button data exists

    Benefits of hierarchical priority:
    - User training persists indefinitely (no drift from auto-learning)
    - Solves the "Keller-Lager" problem where auto-learning corrupts calibration
    - Auto-learning provides fallback for untrained scanner pairs

    Attributes:
        scanner_address: MAC address of the "other" scanner being correlated.

    """

    scanner_address: str
    # Two parallel Kalman filters for hierarchical priority
    _kalman_auto: KalmanFilter = field(
        default_factory=lambda: KalmanFilter(
            process_noise=DELTA_PROCESS_NOISE,
            measurement_noise=DELTA_MEASUREMENT_NOISE,
        ),
        repr=False,
    )
    _kalman_button: KalmanFilter = field(
        default_factory=lambda: KalmanFilter(
            process_noise=DELTA_PROCESS_NOISE,
            measurement_noise=DELTA_MEASUREMENT_NOISE,
        ),
        repr=False,
    )

    def update(self, observed_delta: float) -> float:
        """
        Update correlation with new observed delta from automatic learning.

        The auto filter continuously learns and adapts to environment changes.
        Its influence on the final estimate is weighted against button training.

        Args:
            observed_delta: Current (primary_rssi - other_rssi) value.

        Returns:
            Updated fused estimate of the expected delta.

        """
        self._kalman_auto.update(observed_delta)
        return self.expected_delta

    def update_button(self, observed_delta: float) -> float:
        """
        Update correlation with button-trained delta (The Frozen Layer).

        This creates a "frozen" state that completely overrides auto-learning.
        The button filter is set to extremely high confidence (variance=0.01)
        and high sample count (500) to ensure it dominates any future operations.

        IMPORTANT: Once this is called, the auto-filter becomes "shadow only" -
        it continues learning but has NO INFLUENCE on expected_delta.

        Args:
            observed_delta: Current (primary_rssi - other_rssi) value.

        Returns:
            The frozen button estimate (user truth).

        """
        # Use reset_to_value to create a frozen, high-confidence state
        # - variance=0.01: Extremely high confidence
        # - sample_count=500: Massive inertia (would need ~500 contrary samples to shift)
        self._kalman_button.reset_to_value(
            value=observed_delta,
            variance=0.01,
            sample_count=500,
        )
        return self.expected_delta

    @property
    def expected_delta(self) -> float:
        """
        Return expected delta using HIERARCHICAL PRIORITY (not fusion).

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
        """
        Return total sample count for maturity checks.

        Simple sum of both filter sample counts.
        """
        return self.auto_sample_count + self.button_sample_count

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

    def reset_training(self) -> None:
        """
        Reset user training data (button filter only).

        This reverts the correlation to use automatic learning (Shadow Learning)
        immediately. The auto-filter is preserved, providing a fallback.

        Use this to undo incorrect manual training without losing the
        automatically learned patterns.
        """
        self._kalman_button.reset()

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

        Stores both Kalman filter states for proper restoration.
        """
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
            "estimate": self.expected_delta,
            "variance": self.variance,
            "samples": self.sample_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """
        Deserialize from dictionary.

        Handles both old format (single Kalman) and new format (dual Kalman).
        Old data is migrated to auto filter only.

        Args:
            data: Dictionary from to_dict().

        Returns:
            Restored ScannerPairCorrelation instance.

        """
        corr = cls(scanner_address=data["scanner"])

        # Check for new dual-filter format
        if "auto_estimate" in data:
            # New format: restore both filters
            corr._kalman_auto.estimate = data["auto_estimate"]
            corr._kalman_auto.variance = data["auto_variance"]
            corr._kalman_auto.sample_count = data["auto_samples"]
            corr._kalman_auto._initialized = data["auto_samples"] > 0  # noqa: SLF001

            corr._kalman_button.estimate = data["button_estimate"]
            corr._kalman_button.variance = data["button_variance"]
            corr._kalman_button.sample_count = data["button_samples"]
            corr._kalman_button._initialized = data["button_samples"] > 0  # noqa: SLF001
        else:
            # Old format: migrate to auto filter only
            corr._kalman_auto.estimate = data["estimate"]
            corr._kalman_auto.variance = data["variance"]
            corr._kalman_auto.sample_count = data["samples"]
            corr._kalman_auto._initialized = data["samples"] > 0  # noqa: SLF001
            # Button filter stays uninitialized

        return corr
