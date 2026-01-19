"""
Absolute RSSI tracking for individual scanners.

Tracks the expected RSSI value from a specific scanner when a device
is confirmed in an area. Unlike the delta-based ScannerPairCorrelation,
this tracks absolute values which can be used even when the primary
scanner goes offline.

This enables "room fingerprinting" - even without the primary scanner,
we can verify if secondary scanner readings match the learned pattern.
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
    _kalman: KalmanFilter = field(
        default_factory=lambda: KalmanFilter(
            process_noise=RSSI_PROCESS_NOISE,
            measurement_noise=RSSI_MEASUREMENT_NOISE,
        ),
        repr=False,
    )

    def update(self, rssi: float) -> float:
        """
        Update with new observed absolute RSSI.

        Args:
            rssi: Current absolute RSSI value from this scanner.

        Returns:
            Updated Kalman estimate of expected RSSI.

        """
        return self._kalman.update(rssi)

    @property
    def expected_rssi(self) -> float:
        """Return learned expected absolute RSSI value."""
        return self._kalman.estimate

    @property
    def variance(self) -> float:
        """Return current uncertainty (variance) in the estimate."""
        return self._kalman.variance

    @property
    def std_dev(self) -> float:
        """Return standard deviation of the estimate."""
        return float(self.variance**0.5)

    @property
    def sample_count(self) -> int:
        """Return number of samples processed."""
        return self._kalman.sample_count

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
        """
        Serialize to dictionary for persistent storage.

        Returns:
            Dictionary with scanner address and Kalman state.

        """
        return {
            "scanner": self.scanner_address,
            "estimate": self._kalman.estimate,
            "variance": self._kalman.variance,
            "samples": self._kalman.sample_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """
        Deserialize from dictionary.

        Args:
            data: Dictionary from to_dict().

        Returns:
            Restored ScannerAbsoluteRssi instance.

        """
        profile = cls(scanner_address=data["scanner"])
        profile._kalman.estimate = data["estimate"]
        profile._kalman.variance = data["variance"]
        profile._kalman.sample_count = data["samples"]
        profile._kalman._initialized = data["samples"] > 0  # noqa: SLF001
        return profile
