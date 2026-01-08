"""
Bermuda BLE RSSI Filters Module.

This module provides filtering and statistical analysis components for
BLE RSSI signal processing. The architecture is designed to be modular,
allowing different filter implementations to be swapped as needed.

Current components:
- AdaptiveStatistics: Online mean/variance estimation with CUSUM changepoint detection

Future extensibility:
- KalmanFilter: Standard Kalman filter for RSSI smoothing
- ParticleFilter: For non-Gaussian noise models
- BayesianFilter: Full Bayesian inference for complex scenarios

Usage:
    from custom_components.bermuda.filters import AdaptiveStatistics
    from custom_components.bermuda.filters.const import CALIBRATION_MIN_SAMPLES
"""

from .adaptive import AdaptiveStatistics
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
    # Classes
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
