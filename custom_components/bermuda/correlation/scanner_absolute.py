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
        Update with button-trained RSSI value.

        Unlike auto-learning which adds one sample at a time continuously,
        button training is called multiple times in quick succession (10x).
        Each sample is added to the button Kalman filter using update(),
        allowing all samples to contribute to the estimate.

        This fixes the previous bug where reset_to_value() was used, which
        OVERWROTE previous samples - only the last sample counted, but it
        claimed 500 samples worth of confidence!

        Args:
            rssi: Current absolute RSSI value from this scanner.

        Returns:
            The fused estimate (button + limited auto refinement).

        """
        # Use update() to ADD this sample to the button filter
        # This way all 10 training samples contribute to the average
        self._kalman_button.update(rssi)
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
    def has_button_training(self) -> bool:
        """Check if this profile has been button-trained by the user."""
        return self._kalman_button.is_initialized

    @property
    def is_mature(self) -> bool:
        """
        Check if profile has enough data to be trusted.

        A profile is considered mature if:
        1. It has enough total samples (auto + button >= MIN_SAMPLES_FOR_MATURITY), OR
        2. It has been explicitly button-trained by the user (any amount).

        The second condition is critical for scannerless rooms which have NO auto-learning
        data (no scanner in that room to generate samples). Button training represents
        USER INTENT and should be trusted even with just 10 samples.

        BUG 12 FIX: Without this, scannerless room profiles were never mature
        (10 button samples < 20 maturity threshold) and were skipped by UKF matching.

        Returns:
            True if profile is mature or has button training.

        """
        # Button training = user explicitly said "this is correct" - trust it
        if self.has_button_training:
            return True

        # Standard maturity check for auto-learned profiles
        return self.sample_count >= MIN_SAMPLES_FOR_MATURITY

    def reset_training(self) -> None:
        """
        Reset ALL learned data (button AND auto filters).

        This provides a clean slate for this scanner profile. After reset:
        - Button filter: Cleared, ready for new training
        - Auto filter: Cleared, will re-learn in correct context

        Why reset both? The auto-learned data may be "poisoned" by incorrect
        room selection. After new button training, auto-learning will start
        fresh and learn patterns in the CORRECT context (via the indirect
        feedback loop where room selection influences what auto learns).

        Use this to completely undo incorrect training.
        """
        self._kalman_button.reset()
        self._kalman_auto.reset()

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
        Uses KalmanFilter.restore_state() for clean state restoration.
        """
        profile = cls(scanner_address=data["scanner"])

        if "auto_estimate" in data:
            # New format - use restore_state() for clean deserialization
            profile._kalman_auto.restore_state(
                estimate=data["auto_estimate"],
                variance=data["auto_variance"],
                sample_count=data["auto_samples"],
            )
            profile._kalman_button.restore_state(
                estimate=data["button_estimate"],
                variance=data["button_variance"],
                sample_count=data["button_samples"],
            )
        else:
            # Old format: migrate to auto filter
            profile._kalman_auto.restore_state(
                estimate=data["estimate"],
                variance=data["variance"],
                sample_count=data["samples"],
            )

        return profile
