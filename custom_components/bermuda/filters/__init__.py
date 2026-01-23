"""
Bermuda BLE RSSI Filters Module.

This module provides modular, swappable signal filters for BLE RSSI processing.
All filters implement the SignalFilter interface for interoperability.

Architecture:
------------
    SignalFilter (ABC)              # Abstract base class
        ├── KalmanFilter            # Classic linear Kalman filter
        ├── AdaptiveRobustFilter    # EMA + CUSUM changepoint detection
        └── UnscentedKalmanFilter   # Multi-scanner fusion (experimental)

Features:
---------
- **Time-Aware Filtering**: Process noise scales with time delta for irregular
  BLE advertisement intervals. Longer gaps = more uncertainty.
- **Factory Function**: create_filter("kalman") for configuration-driven creation.
- **Serialization**: to_dict()/from_dict() for KalmanFilter persistence.
- **NumPy Acceleration**: Optional 10-100x speedup for UKF with 20+ scanners.
- **Sequential Update**: O(n²) alternative to O(n³) UKF update for partial observations.

Usage:
------
    # Using factory function (recommended)
    from custom_components.bermuda.filters import create_filter
    filter = create_filter("kalman")
    filtered = filter.update(rssi_raw)

    # Time-aware filtering (mathematically more correct)
    filter.update(rssi_raw, timestamp=time.time())

    # Using specific filter directly
    from custom_components.bermuda.filters import KalmanFilter
    filter = KalmanFilter()
    filtered = filter.update(rssi_raw)

    # Serialize and restore
    state = filter.to_dict()
    restored = KalmanFilter.from_dict(state)

    # UKF for multi-scanner fusion
    from custom_components.bermuda.filters import UnscentedKalmanFilter
    ukf = UnscentedKalmanFilter()
    ukf.update_multi({"scanner1": -70, "scanner2": -75}, timestamp=time.time())

    # Using constants
    from custom_components.bermuda.filters import CALIBRATION_MIN_SAMPLES

Available Filters:
-----------------
- KalmanFilter: Classic Kalman filter, optimal for Gaussian noise
- AdaptiveRobustFilter: EMA-based with CUSUM changepoint detection
- UnscentedKalmanFilter: Multi-scanner fusion with fingerprint matching
- AdaptiveStatistics: Low-level stats class (used internally)

Future Filters (planned):
------------------------
- RobustKalmanFilter: Kalman with outlier rejection
- ParticleFilter: Full Bayesian, handles non-Gaussian noise
- HuberFilter: Robust M-estimation
"""

# Base classes and interfaces
# Filter implementations
from .adaptive import AdaptiveRobustFilter, AdaptiveStatistics
from .base import FilterConfig, SignalFilter, create_filter

# Constants
from .const import (
    ADAPTIVE_MIN_NOISE_MULTIPLIER,
    ADAPTIVE_NOISE_SCALE_PER_10DB,
    ADAPTIVE_RSSI_OFFSET_FROM_REF,
    BLE_RSSI_TYPICAL_STDDEV,
    CALIBRATION_HYSTERESIS_DB,
    CALIBRATION_MAX_HISTORY,
    CALIBRATION_MIN_PAIRS,
    CALIBRATION_MIN_SAMPLES,
    CUSUM_DRIFT_SIGMA,
    CUSUM_THRESHOLD_SIGMA,
    DEFAULT_UPDATE_DT,
    EMA_ALPHA_FAST,
    EMA_ALPHA_SLOW,
    KALMAN_MEASUREMENT_NOISE,
    KALMAN_PROCESS_NOISE,
    MAX_UPDATE_DT,
    MIN_UPDATE_DT,
)
from .kalman import KalmanFilter
from .ukf import UnscentedKalmanFilter

__all__ = [
    # Constants
    "ADAPTIVE_MIN_NOISE_MULTIPLIER",
    "ADAPTIVE_NOISE_SCALE_PER_10DB",
    "ADAPTIVE_RSSI_OFFSET_FROM_REF",
    "BLE_RSSI_TYPICAL_STDDEV",
    "CALIBRATION_HYSTERESIS_DB",
    "CALIBRATION_MAX_HISTORY",
    "CALIBRATION_MIN_PAIRS",
    "CALIBRATION_MIN_SAMPLES",
    "CUSUM_DRIFT_SIGMA",
    "CUSUM_THRESHOLD_SIGMA",
    "DEFAULT_UPDATE_DT",
    "EMA_ALPHA_FAST",
    "EMA_ALPHA_SLOW",
    "KALMAN_MEASUREMENT_NOISE",
    "KALMAN_PROCESS_NOISE",
    "MAX_UPDATE_DT",
    "MIN_UPDATE_DT",
    # Classes
    "AdaptiveRobustFilter",
    "AdaptiveStatistics",
    "FilterConfig",
    "KalmanFilter",
    "SignalFilter",
    "UnscentedKalmanFilter",
    # Factory function
    "create_filter",
]
