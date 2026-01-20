"""
Single scanner-pair correlation tracking.

Wraps Kalman filters to track the typical RSSI delta between
a primary scanner and another scanner when a device is in a specific area.

This module is part of the scanner correlation learning system that
improves area localization by learning spatial relationships between scanners.

Weighted Learning System (Two-Pool Fusion with Inverse-Variance Weighting):
    - Two parallel Kalman filters: one for automatic learning, one for button training
    - Weights are determined by INVERSE VARIANCE (mathematically optimal)
    - Lower variance = higher confidence = more weight
    - Button training with consistent values naturally dominates over noisy auto learning
    - Both pools continue learning indefinitely - no hard caps that block new data

Mathematical basis (optimal Bayesian fusion):
    weight_i = 1 / variance_i
    fused_estimate = Σ(estimate_i * weight_i) / Σ(weight_i)
    fused_variance = 1 / Σ(1 / variance_i)
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

    Uses two parallel Kalman filters with INVERSE-VARIANCE fusion:
    - Auto filter: Continuously learns from automatic room detection
    - Button filter: Learns from manual button training

    The final estimate uses mathematically optimal Bayesian fusion:
    - Weight = 1 / variance (lower uncertainty = more trust)
    - Button training with consistent values (low variance) naturally dominates
    - No artificial multipliers - the math handles it automatically

    Benefits of inverse-variance weighting:
    - Few confident button samples can override many noisy auto samples
    - Self-regulating: quality matters more than quantity
    - Adapts to changing conditions in both pools

    Attributes:
        scanner_address: MAC address of the "other" scanner being correlated.

    """

    scanner_address: str
    # Two parallel Kalman filters for weighted fusion
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
        Update correlation with button-trained delta.

        FIX: Fehler 2 - Inflate auto-filter variance when button training occurs,
        but only if auto-filter is already converged (low variance). This ensures
        button training can override accumulated auto-learning bias without
        destroying the auto-learning capability entirely.

        Args:
            observed_delta: Current (primary_rssi - other_rssi) value.

        Returns:
            Updated fused estimate of the expected delta.

        """
        # FIX: Inflate auto-filter variance only if it's converged (variance < 5.0).
        # A converged auto-filter has thousands of samples and would otherwise
        # dominate the button training due to inverse-variance weighting.
        # By inflating variance when converged, we "forget" some auto-confidence
        # and allow button training to take precedence.
        # We don't inflate if variance is already high (unconverged or already inflated).
        converged_threshold = 5.0
        if self._kalman_auto.sample_count > 0 and self._kalman_auto.variance < converged_threshold:
            # Inflate to ~15.0 which is roughly the initial/unconverged state
            self._kalman_auto.variance = 15.0

        self._kalman_button.update(observed_delta)
        return self.expected_delta

    @property
    def expected_delta(self) -> float:
        """
        Return inverse-variance weighted fusion of auto and button estimates.

        Uses mathematically optimal Bayesian sensor fusion:
            weight_i = 1 / variance_i
            fused = Σ(estimate_i * weight_i) / Σ(weight_i)

        This means:
        - Lower variance (higher confidence) = more weight
        - Button training with consistent values naturally dominates
        - Self-regulating without arbitrary multipliers

        If only one filter has data, returns that filter's estimate.
        If neither has data, returns 0.0.
        """
        auto_samples = self._kalman_auto.sample_count
        button_samples = self._kalman_button.sample_count

        # Handle cases where one or both filters have no data
        if auto_samples == 0 and button_samples == 0:
            return 0.0
        if button_samples == 0:
            return self._kalman_auto.estimate
        if auto_samples == 0:
            return self._kalman_button.estimate

        # Inverse-variance weighting (optimal Bayesian fusion)
        auto_var = max(self._kalman_auto.variance, MIN_VARIANCE)
        button_var = max(self._kalman_button.variance, MIN_VARIANCE)

        auto_weight = 1.0 / auto_var
        button_weight = 1.0 / button_var
        total_weight = auto_weight + button_weight

        return (self._kalman_auto.estimate * auto_weight + self._kalman_button.estimate * button_weight) / total_weight

    @property
    def variance(self) -> float:
        """
        Return combined variance from both filters.

        Uses inverse-variance fusion formula:
            combined_variance = 1 / (1/var1 + 1/var2)

        This is mathematically optimal for Gaussian distributions.
        """
        # If either filter is uninitialized, use the other
        if self._kalman_auto.sample_count == 0:
            return self._kalman_button.variance
        if self._kalman_button.sample_count == 0:
            return self._kalman_auto.variance

        auto_var = max(self._kalman_auto.variance, MIN_VARIANCE)
        button_var = max(self._kalman_button.variance, MIN_VARIANCE)

        # Inverse-variance fusion (standard formula)
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
