"""
Single scanner-pair correlation tracking.

Wraps Kalman filters to track the typical RSSI delta between
a primary scanner and another scanner when a device is in a specific area.

This module is part of the scanner correlation learning system that
improves area localization by learning spatial relationships between scanners.

Clamped Bayesian Fusion (Controlled Evolution):
    - Two parallel Kalman filters: one for automatic learning, one for button training
    - Button training sets the "anchor" (user truth)
    - Auto-learning can "polish" the anchor but NEVER overpower it
    - Auto-influence is clamped to maximum 30% (user keeps 70%+ authority)
    - This allows intelligent refinement while preventing anchor drift

Fusion Logic:
    - If only auto data exists → use auto estimate (100% auto)
    - If button data exists → fuse both, BUT clamp auto influence to max 30%
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Self

from custom_components.bermuda.const import AUTO_LEARNING_VARIANCE_FLOOR
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

# Maximum influence ratio for auto-learning when button training exists.
# User always retains at least (1 - MAX_AUTO_RATIO) = 70% authority.
MAX_AUTO_RATIO: float = 0.30


@dataclass(slots=True)
class ScannerPairCorrelation:
    """
    Tracks learned RSSI delta from primary scanner to another scanner.

    The delta is defined as: primary_rssi - other_rssi
    Positive delta means the other scanner sees a weaker signal.

    Uses CLAMPED BAYESIAN FUSION (Controlled Evolution):
    - Auto filter: Continuously learns from automatic room detection
    - Button filter: Sets the "anchor" from manual button training

    Fusion Logic:
    - If only auto data exists → use auto estimate (100% auto)
    - If button data exists → fuse both, BUT clamp auto influence to max 30%
    - User always retains at least 70% authority over the final estimate

    Benefits of clamped fusion:
    - User training dominates (at least 70% weight)
    - Auto-learning can "polish" the anchor (adapt to small changes)
    - Prevents long-term drift while allowing intelligent refinement
    - Solves the "Keller-Lager" problem while enabling controlled evolution

    Attributes
    ----------
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

    def update(self, observed_delta: float, timestamp: float | None = None) -> float:
        """
        Update correlation with new observed delta from automatic learning.

        The auto filter continuously learns and adapts to environment changes.
        Its influence on the final estimate is weighted against button training.

        Args:
        ----
            observed_delta: Current (primary_rssi - other_rssi) value.
            timestamp: Optional timestamp for profile age tracking.
                      When provided, enables first/last sample tracking.

        Returns:
        -------
            Updated fused estimate of the expected delta.

        """
        self._kalman_auto.update(observed_delta, timestamp=timestamp)

        # Variance Floor: Prevent unbounded convergence that causes z-score explosion.
        # Without this, after thousands of samples variance approaches 0, making normal
        # BLE fluctuations (3-5dB) appear as 10+ sigma deviations.
        self._kalman_auto.variance = max(self._kalman_auto.variance, AUTO_LEARNING_VARIANCE_FLOOR)

        return self.expected_delta

    def update_button(self, observed_delta: float, timestamp: float | None = None) -> float:
        """
        Update correlation with button-trained delta.

        Unlike auto-learning which adds one sample at a time continuously,
        button training is called multiple times in quick succession (10x).
        Each sample is added to the button Kalman filter using update(),
        allowing all samples to contribute to the estimate.

        This fixes the previous bug where reset_to_value() was used, which
        OVERWROTE previous samples - only the last sample counted, but it
        claimed 500 samples worth of confidence!

        Args:
        ----
            observed_delta: Current (primary_rssi - other_rssi) value.
            timestamp: Optional timestamp for profile age tracking.
                      When provided, enables first/last sample tracking.

        Returns:
        -------
            The fused estimate (button + limited auto refinement).

        """
        # Use update() to ADD this sample to the button filter
        # This way all 10 training samples contribute to the average
        self._kalman_button.update(observed_delta, timestamp=timestamp)
        return self.expected_delta

    @property
    def expected_delta(self) -> float:
        """
        Return expected delta using CLAMPED BAYESIAN FUSION.

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

        # Apply same clamping as in expected_delta
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
        """
        Return total sample count for maturity checks.

        Simple sum of both filter sample counts.
        """
        return self.auto_sample_count + self.button_sample_count

    @property
    def has_button_training(self) -> bool:
        """Check if this profile has been button-trained by the user."""
        return self._kalman_button.is_initialized

    @property
    def first_sample_stamp(self) -> float | None:
        """
        Return earliest timestamp from either filter.

        Used for profile age tracking - when was this profile first created.
        Returns None if no samples have timestamps.
        """
        auto_first = self._kalman_auto.first_sample_stamp
        btn_first = self._kalman_button.first_sample_stamp

        if auto_first is None and btn_first is None:
            return None
        if auto_first is None:
            return btn_first
        if btn_first is None:
            return auto_first
        return min(auto_first, btn_first)

    @property
    def last_sample_stamp(self) -> float | None:
        """
        Return latest timestamp from either filter.

        Used for profile age tracking - when was this profile last updated.
        Returns None if no samples have timestamps.
        """
        auto_last = self._kalman_auto.last_sample_stamp
        btn_last = self._kalman_button.last_sample_stamp

        if auto_last is None and btn_last is None:
            return None
        if auto_last is None:
            return btn_last
        if btn_last is None:
            return auto_last
        return max(auto_last, btn_last)

    @property
    def is_mature(self) -> bool:
        """
        Check if correlation has enough data to be trusted.

        A profile is considered mature if:
        1. It has enough total samples (auto + button >= MIN_SAMPLES_FOR_MATURITY), OR
        2. It has been explicitly button-trained by the user (any amount).

        The second condition is critical for scannerless rooms which have NO auto-learning
        data (no scanner in that room to generate samples). Button training represents
        USER INTENT and should be trusted even with just 10 samples.

        BUG 12 FIX: Without this, scannerless room profiles were never mature
        (10 button samples < 30 maturity threshold) and were skipped by UKF matching.

        Returns
        -------
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

        This provides a clean slate for this correlation. After reset:
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

    def reset_variance_only(self) -> None:
        """
        Reset button filter variance while preserving estimate.

        Used for multi-position training within the same room. When a user
        trains from a new position, we want the new samples to have equal
        influence to previous training, not diminishing influence.

        Only resets the button filter (user training), not auto filter.
        """
        self._kalman_button.reset_variance_only()

    def z_score(self, observed_delta: float) -> float:
        """
        Calculate deviation from expectation in standard deviations.

        Args:
        ----
            observed_delta: Currently observed delta to compare.

        Returns:
        -------
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
            "auto_first_stamp": self._kalman_auto.first_sample_stamp,
            "auto_last_stamp": self._kalman_auto.last_sample_stamp,
            # Button filter state
            "button_estimate": self._kalman_button.estimate,
            "button_variance": self._kalman_button.variance,
            "button_samples": self._kalman_button.sample_count,
            "button_first_stamp": self._kalman_button.first_sample_stamp,
            "button_last_stamp": self._kalman_button.last_sample_stamp,
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
        Uses KalmanFilter.restore_state() for clean state restoration.

        Args:
        ----
            data: Dictionary from to_dict().

        Returns:
        -------
            Restored ScannerPairCorrelation instance.

        Raises:
        ------
            TypeError: If scanner address is not a string.
            ValueError: If data contains invalid values (negative variance, etc.)
            KeyError: If required fields are missing.

        """
        scanner_addr = data["scanner"]
        if not isinstance(scanner_addr, str):
            msg = f"scanner must be str, got {type(scanner_addr).__name__}"
            raise TypeError(msg)

        corr = cls(scanner_address=scanner_addr)

        # Check for new dual-filter format
        if "auto_estimate" in data:
            # New format - validate and restore
            auto_var = float(data["auto_variance"])
            auto_samples = int(data["auto_samples"])
            btn_var = float(data["button_variance"])
            btn_samples = int(data["button_samples"])

            if auto_var < 0 or btn_var < 0:
                msg = "variance must be non-negative"
                raise ValueError(msg)
            if auto_samples < 0 or btn_samples < 0:
                msg = "sample_count must be non-negative"
                raise ValueError(msg)

            corr._kalman_auto.restore_state(
                estimate=float(data["auto_estimate"]),
                variance=auto_var,
                sample_count=auto_samples,
            )
            # Restore profile age timestamps
            corr._kalman_auto.first_sample_stamp = data.get("auto_first_stamp")
            corr._kalman_auto.last_sample_stamp = data.get("auto_last_stamp")

            corr._kalman_button.restore_state(
                estimate=float(data["button_estimate"]),
                variance=btn_var,
                sample_count=btn_samples,
            )
            # Restore profile age timestamps
            corr._kalman_button.first_sample_stamp = data.get("button_first_stamp")
            corr._kalman_button.last_sample_stamp = data.get("button_last_stamp")
        else:
            # Old format: validate and migrate to auto filter only
            variance = float(data["variance"])
            samples = int(data["samples"])

            if variance < 0:
                msg = "variance must be non-negative"
                raise ValueError(msg)
            if samples < 0:
                msg = "sample_count must be non-negative"
                raise ValueError(msg)

            corr._kalman_auto.restore_state(
                estimate=float(data["estimate"]),
                variance=variance,
                sample_count=samples,
            )
            # Button filter stays uninitialized

        return corr
