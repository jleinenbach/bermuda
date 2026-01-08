"""
Bermuda BLE RSSI Filters Module.

This module provides modular, swappable signal filters for BLE RSSI processing.
All filters implement the SignalFilter interface for interoperability.

Architecture:
------------
    SignalFilter (ABC)              # Abstract base class
        ├── KalmanFilter            # Classic linear Kalman filter
        ├── AdaptiveRobustFilter    # EMA + CUSUM changepoint detection
        └── (future filters...)

Usage:
------
    # Using specific filter
    from custom_components.bermuda.filters import KalmanFilter
    filter = KalmanFilter()
    filtered = filter.update(rssi_raw)

    # Using filter interface for swappable backends
    from custom_components.bermuda.filters import SignalFilter, KalmanFilter
    def process(filter: SignalFilter, measurements: list[float]):
        return [filter.update(m) for m in measurements]

    # Using constants
    from custom_components.bermuda.filters import CALIBRATION_MIN_SAMPLES

Available Filters:
-----------------
- KalmanFilter: Classic Kalman filter, optimal for Gaussian noise
- AdaptiveRobustFilter: EMA-based with CUSUM changepoint detection
- AdaptiveStatistics: Low-level stats class (used internally)

Future Filters (planned):
------------------------
- RobustKalmanFilter: Kalman with outlier rejection
- ParticleFilter: Full Bayesian, handles non-Gaussian noise
- HuberFilter: Robust M-estimation
"""

# Base classes and interfaces
from .base import FilterConfig, SignalFilter

# Filter implementations
from .adaptive import AdaptiveRobustFilter, AdaptiveStatistics
from .kalman import KalmanFilter

# Constants
from .const import (
    BLE_RSSI_TYPICAL_STDDEV,
    CALIBRATION_MAX_HISTORY,
    CALIBRATION_MIN_PAIRS,
    CALIBRATION_MIN_SAMPLES,
    CUSUM_DRIFT_SIGMA,
    CUSUM_THRESHOLD_SIGMA,
    EMA_ALPHA_FAST,
    EMA_ALPHA_SLOW,
    KALMAN_MEASUREMENT_NOISE,
    KALMAN_PROCESS_NOISE,
)

__all__ = [
    # Base classes
    "SignalFilter",
    "FilterConfig",
    # Filter implementations
    "KalmanFilter",
    "AdaptiveRobustFilter",
    "AdaptiveStatistics",
    # Constants - BLE characteristics
    "BLE_RSSI_TYPICAL_STDDEV",
    # Constants - Kalman
    "KALMAN_PROCESS_NOISE",
    "KALMAN_MEASUREMENT_NOISE",
    # Constants - EMA
    "EMA_ALPHA_SLOW",
    "EMA_ALPHA_FAST",
    # Constants - CUSUM
    "CUSUM_THRESHOLD_SIGMA",
    "CUSUM_DRIFT_SIGMA",
    # Constants - Calibration
    "CALIBRATION_MIN_SAMPLES",
    "CALIBRATION_MAX_HISTORY",
    "CALIBRATION_MIN_PAIRS",
]
