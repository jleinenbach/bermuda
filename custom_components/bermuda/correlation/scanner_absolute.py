"""
Absolute RSSI tracking for individual scanners.

Tracks the expected RSSI value from a specific scanner when a device
is confirmed in an area. Unlike the delta-based ScannerPairCorrelation,
this tracks absolute values which can be used even when the primary
scanner goes offline.

This enables "room fingerprinting" - even without the primary scanner,
we can verify if secondary scanner readings match the learned pattern.

Clamped Bayesian Fusion (Controlled Evolution):
    - Two parallel Kalman filters: one for automatic learning, one for button training
    - Button training sets the "anchor" (user truth)
    - Auto-learning can "polish" the anchor but NEVER overpower it
    - Auto-influence is clamped to maximum 30% (user keeps 70%+ authority)
    - This allows intelligent refinement while preventing anchor drift
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

# Maximum influence ratio for auto-learning when button training exists.
# User always retains at least (1 - MAX_AUTO_RATIO) = 70% authority.
MAX_AUTO_RATIO: float = 0.30


@dataclass(slots=True)
class ScannerAbsoluteRssi:
    """
    Tracks expected absolute RSSI from a scanner in an area.

    When a device is confirmed in an area, we track the absolute RSSI
    values seen from each scanner (not deltas). This allows us to
    verify if the device is still in the same area even when the
    primary scanner goes offline.

    Uses CLAMPED BAYESIAN FUSION (Controlled Evolution):
    - Auto filter: Continuously learns from automatic room detection
    - Button filter: Sets the "anchor" from manual button training

    Fusion Logic:
    - If only auto data exists → use auto estimate (100% auto)
    - If button data exists → fuse both, BUT clamp auto influence to max 30%
    - User always retains at least 70% authority over the final estimate

    This allows auto-learning to "polish" the user's anchor (adapt to small
    environmental changes) while preventing it from "drifting away" over time.

    Example:
        User trains device in "Keller" (cellar) at -85dB:
        - Button filter: -85dB (the anchor, ~70-95% weight)
        - Auto filter drifts to -80dB over months
        - expected_rssi returns ~-83.5dB (polished, not overwritten)
        - Room detection stays stable while adapting slightly

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
        Update with button-trained RSSI value (The Anchor).

        This creates a high-confidence anchor state. The button filter is set
        to high confidence (variance=2.0, σ≈1.4dB) and high sample count (500).

        IMPORTANT: Variance serves TWO purposes:
        1. Fusion weighting: Lower variance = higher weight in Clamped Fusion
        2. Z-Score matching: Variance defines what counts as "acceptable" deviation

        We use variance=2.0 (σ≈1.4dB) because:
        - It's MUCH lower than typical auto variance (16-25), ensuring fusion dominance
        - It's PHYSICALLY REALISTIC: BLE signals fluctuate 2-5dB normally
        - A variance of 0.1 would make 2dB deviation = 6 sigma = "impossible" → room rejected!

        With Clamped Fusion, auto-learning can still refine the result, but
        its influence is clamped to max 30% - the user anchor dominates.

        Args:
            rssi: Current absolute RSSI value from this scanner.

        Returns:
            The fused estimate (anchor + limited auto refinement).

        """
        # Use reset_to_value to create a high-confidence anchor state
        # - variance=2.0: High confidence (σ≈1.4dB) but physically realistic for BLE
        # - sample_count=500: Massive inertia as base
        # NOTE: Do NOT use variance < 1.0! See "Hyper-Precision Paradox" in CLAUDE.md
        self._kalman_button.reset_to_value(
            value=rssi,
            variance=2.0,
            sample_count=500,
        )
        return self.expected_rssi

    @property
    def expected_rssi(self) -> float:
        """
        Return expected RSSI using CLAMPED BAYESIAN FUSION.

        Algorithm:
        1. If only auto data → return auto estimate (100% auto)
        2. If button data exists → fuse with clamped auto influence
           - Calculate inverse-variance weights (standard Bayes)
           - Clamp auto weight to max 30% of total
           - User anchor retains at least 70% authority

        This allows auto-learning to "polish" the user anchor while
        preventing long-term drift from overwhelming user calibration.
        """
        # Case 1: Only auto data available
        if not self._kalman_button.is_initialized:
            if self._kalman_auto.is_initialized:
                return self._kalman_auto.estimate
            return 0.0

        # Case 2: Button data exists - use Clamped Fusion
        est_btn = self._kalman_button.estimate
        est_auto = self._kalman_auto.estimate if self._kalman_auto.is_initialized else est_btn

        # Variance protection (division by zero prevention)
        var_btn = max(self._kalman_button.variance, 1e-6)
        var_auto = max(self._kalman_auto.variance, 1e-6) if self._kalman_auto.is_initialized else var_btn

        # Standard Inverse Variance Weights (Bayes optimal)
        w_btn = 1.0 / var_btn
        w_auto = 1.0 / var_auto

        # --- CLAMPING LOGIC ---
        # Goal: w_auto / (w_btn + w_auto) <= MAX_AUTO_RATIO (0.30)
        current_auto_ratio = w_auto / (w_btn + w_auto)

        if current_auto_ratio > MAX_AUTO_RATIO:
            # Auto is too strong! Scale it down.
            # Formula derived from: w_new / (w_btn + w_new) = MAX_AUTO_RATIO
            # => w_new = (MAX_AUTO_RATIO / (1 - MAX_AUTO_RATIO)) * w_btn
            ratio_factor = MAX_AUTO_RATIO / (1.0 - MAX_AUTO_RATIO)  # 0.3/0.7 ≈ 0.428
            w_auto = w_btn * ratio_factor

        # Weighted average with (potentially clamped) w_auto
        total_weight = w_btn + w_auto
        return (est_btn * w_btn + est_auto * w_auto) / total_weight

    @property
    def variance(self) -> float:
        """
        Return fused variance using Clamped Bayesian Fusion.

        When button data exists, returns the combined variance from
        the clamped fusion. This reflects the reduced uncertainty
        from having both user anchor and auto refinement.
        """
        # Case 1: Only auto data
        if not self._kalman_button.is_initialized:
            return self._kalman_auto.variance

        # Case 2: Clamped Fusion - compute fused variance
        var_btn = max(self._kalman_button.variance, 1e-6)
        var_auto = max(self._kalman_auto.variance, 1e-6) if self._kalman_auto.is_initialized else var_btn

        w_btn = 1.0 / var_btn
        w_auto = 1.0 / var_auto

        # Apply same clamping as in expected_rssi
        current_auto_ratio = w_auto / (w_btn + w_auto)
        if current_auto_ratio > MAX_AUTO_RATIO:
            ratio_factor = MAX_AUTO_RATIO / (1.0 - MAX_AUTO_RATIO)
            w_auto = w_btn * ratio_factor

        # Fused variance = 1 / total_weight
        total_weight = w_btn + w_auto
        return 1.0 / total_weight

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

    def reset_training(self) -> None:
        """
        Reset user training data (button filter only).

        This reverts the scanner to use automatic learning (Shadow Learning)
        immediately. The auto-filter is preserved, providing a fallback.

        Use this to undo incorrect manual training without losing the
        automatically learned patterns.
        """
        self._kalman_button.reset()

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
