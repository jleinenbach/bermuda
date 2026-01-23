"""
Abstract base classes for BLE RSSI signal filters.

This module defines the interface that all signal filters must implement,
enabling modular, swappable filter backends for different use cases.

Design Philosophy:
- Filters process sequential RSSI measurements over time
- Each filter maintains its own state (estimate, variance)
- Filters are interchangeable via the SignalFilter interface
- Concrete implementations handle algorithm-specific details

Available Implementations:
- AdaptiveRobustFilter: EMA-based with CUSUM changepoint detection
- KalmanFilter: Classic linear Kalman filter (planned)
- RobustKalmanFilter: Kalman with outlier rejection (planned)
- ParticleFilter: Full Bayesian, handles non-Gaussian noise (planned)
- HuberFilter: Robust M-estimation, efficient (planned)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


class SignalFilter(ABC):
    """
    Abstract base class for swappable signal filters.

    All BLE RSSI filters should implement this interface to enable
    modular filter selection and comparison. The interface supports:

    - Sequential processing of measurements
    - State estimation (filtered value)
    - Uncertainty estimation (variance)
    - Diagnostics export

    Example usage:
        filter = KalmanFilter()
        for rssi in measurements:
            filtered = filter.update(rssi, timestamp)
            print(f"Estimate: {filter.get_estimate()}, Variance: {filter.get_variance()}")
    """

    @abstractmethod
    def update(self, measurement: float, timestamp: float | None = None) -> float:
        """
        Process a new measurement and update filter state.

        Args:
            measurement: Raw RSSI value in dBm (typically -100 to 0)
            timestamp: Optional timestamp for time-aware filtering

        Returns:
            The filtered/smoothed estimate after incorporating this measurement

        """

    @abstractmethod
    def get_estimate(self) -> float:
        """
        Return the current best estimate of the signal.

        Returns:
            Current filtered RSSI estimate in dBm

        """

    @abstractmethod
    def get_variance(self) -> float:
        """
        Return the current uncertainty estimate.

        Returns:
            Variance of the estimate (dBm²). Can be used for:
            - Weighting in multi-sensor fusion
            - Confidence intervals
            - Adaptive algorithm tuning

        """

    @abstractmethod
    def reset(self) -> None:
        """Reset filter state to initial conditions."""

    def get_diagnostics(self) -> dict[str, Any]:
        """
        Return diagnostic information for debugging/monitoring.

        Returns:
            Dictionary with filter-specific diagnostic data.
            Default implementation returns basic state.

        """
        return {
            "estimate": self.get_estimate(),
            "variance": self.get_variance(),
        }


@dataclass
class FilterConfig:
    """
    Configuration for signal filters.

    This provides a standardized way to configure filters,
    with sensible defaults based on BLE RSSI research.
    """

    # Process noise - how much the true signal changes between measurements
    # Lower = smoother output, slower response
    # Higher = noisier output, faster response
    process_noise: float = 0.008  # Typical for static BLE positioning

    # Measurement noise - expected variance of raw measurements
    # Based on BLE RSSI research: 3-6 dBm typical, we use 4² = 16
    measurement_noise: float = 16.0  # 4 dBm std dev squared

    # Initial variance estimate
    initial_variance: float = 16.0

    # EMA alpha for adaptive filters (0 < alpha < 1)
    # Lower = slower adaptation, Higher = faster
    ema_alpha: float = 0.1


def create_filter(
    filter_type: str = "kalman",
    config: FilterConfig | None = None,
) -> SignalFilter:
    """
    Factory method for creating signal filters.

    This provides a convenient way to instantiate filters by name,
    useful for configuration-driven filter selection.

    Args:
        filter_type: Type of filter to create. One of:
            - "kalman": 1D Kalman filter (default, best for most cases)
            - "adaptive": EMA with CUSUM changepoint detection
            - "ukf": Unscented Kalman Filter for multi-scanner fusion
        config: Optional configuration. Uses sensible defaults if None.

    Returns:
        Configured SignalFilter instance.

    Raises:
        ValueError: If filter_type is not recognized.

    Example:
        >>> filter = create_filter("kalman")
        >>> filtered = filter.update(-70.0)

        >>> config = FilterConfig(process_noise=0.01)
        >>> filter = create_filter("kalman", config)

    """
    # Lazy imports to avoid circular dependencies
    from .adaptive import AdaptiveRobustFilter  # noqa: PLC0415
    from .kalman import KalmanFilter  # noqa: PLC0415
    from .ukf import UnscentedKalmanFilter  # noqa: PLC0415

    config = config or FilterConfig()

    factories: dict[str, Callable[[], SignalFilter]] = {
        "kalman": lambda: KalmanFilter.from_config(config),
        "adaptive": lambda: AdaptiveRobustFilter.from_config(config),
        "ukf": lambda: UnscentedKalmanFilter(),
    }

    if filter_type not in factories:
        valid_types = ", ".join(sorted(factories.keys()))
        msg = f"Unknown filter type: '{filter_type}'. Valid types: {valid_types}"
        raise ValueError(msg)

    return factories[filter_type]()
