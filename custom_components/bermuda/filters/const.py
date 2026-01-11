"""
Constants for BLE RSSI filtering and calibration.

These values are derived from peer-reviewed BLE RSSI research:
- Wouter Bulten: Kalman filters for RSSI signals
  https://www.wouterbulten.nl/posts/kalman-filters-explained-removing-noise-from-rssi-signals/
- PMC5461075: BLE Indoor Localization with Kalman-Based Fusion
  https://pmc.ncbi.nlm.nih.gov/articles/PMC5461075/
- neXenio: BLE Indoor Positioning RSSI Measurements
  https://github.com/neXenio/BLE-Indoor-Positioning/wiki/RSSI-Measurements
"""

from typing import Final

# =============================================================================
# BLE RSSI Physical Characteristics
# =============================================================================

# Typical BLE RSSI standard deviation in indoor environments (dBm).
# Research shows 3-6 dBm typical, increases significantly after 3m distance.
# Wall obstructions cause ~6 dBm additional degradation.
BLE_RSSI_TYPICAL_STDDEV: Final = 4.0

# =============================================================================
# Kalman Filter Parameters (for reference/future use)
# =============================================================================

# Process noise covariance (R) - uncertainty in state transition.
# Lower values = more trust in predicted state.
# 0.008 is recommended for static BLE positioning.
KALMAN_PROCESS_NOISE: Final = 0.008

# Measurement noise covariance (Q) - uncertainty in measurements.
# Can be derived from signal variance; 4.0 is typical for BLE RSSI.
KALMAN_MEASUREMENT_NOISE: Final = 4.0

# =============================================================================
# EMA (Exponential Moving Average) Parameters
# =============================================================================

# Alpha for adapting statistical parameters over time.
# Lower values (0.05-0.1) = slower adaptation, more stability
# Higher values (0.2-0.3) = faster adaptation, more responsive
EMA_ALPHA_SLOW: Final = 0.1
EMA_ALPHA_FAST: Final = 0.3

# =============================================================================
# CUSUM Changepoint Detection Parameters
# =============================================================================

# Threshold in standard deviations.
# When cumulative deviation exceeds this, a changepoint is detected.
# Value of 4 sigma balances false alarms vs detection delay (ARL considerations).
# Works correctly when CUSUM is applied to Kalman-filtered values.
CUSUM_THRESHOLD_SIGMA: Final = 4.0

# Drift parameter - prevents cumulative sum from growing in absence of change.
# Expressed as fraction of standard deviation (0.5 sigma per sample).
# Works correctly when CUSUM is applied to Kalman-filtered values.
CUSUM_DRIFT_SIGMA: Final = 0.5

# =============================================================================
# Scanner Calibration Parameters
# =============================================================================

# Minimum cross-visibility samples before trusting scanner pair data.
# 50 samples provides more stable statistics than the original 10.
# At typical update rates (~1Hz), this means ~50 seconds of data.
CALIBRATION_MIN_SAMPLES: Final = 50

# Maximum RSSI history for median calculation.
# 100 samples provides robust median while limiting memory usage.
CALIBRATION_MAX_HISTORY: Final = 100

# Minimum scanner pairs needed to calculate an offset.
# With just 1 pair we can still detect relative sensitivity differences.
CALIBRATION_MIN_PAIRS: Final = 1

# Hysteresis threshold for offset changes (dB).
# Only update suggested offset if the new value differs by more than this.
# Prevents oscillation due to noise around rounding boundaries.
CALIBRATION_HYSTERESIS_DB: Final = 3

# =============================================================================
# Adaptive Kalman Filter Parameters
# =============================================================================
# Based on research showing RSSI measurement variance increases with distance:
# - "Variational Bayesian Adaptive UKF for RSSI-based Indoor Localization"
#   https://link.springer.com/article/10.1007/s12555-019-0973-9
# - PMC5461075: SNR degrades as distance increases
#
# The adaptive filter scales measurement noise based on signal strength:
# R_adaptive = R_base * scale^((threshold - rssi) / 10)
# Where threshold = ref_power - ADAPTIVE_RSSI_OFFSET_FROM_REF

# Offset (dB) from device's ref_power to define "strong signal" threshold.
# Signals within this offset of ref_power are considered strong/reliable.
# 10 dB below ref_power corresponds to ~3m distance (near-field).
# This makes the threshold device-specific, not absolute.
ADAPTIVE_RSSI_OFFSET_FROM_REF: Final = 10.0

# Noise scaling factor per 10 dB signal decrease below threshold.
# For each 10 dB weaker signal, measurement noise multiplies by this factor.
# Value of 1.5 provides moderate scaling that still trusts same-room signals.
ADAPTIVE_NOISE_SCALE_PER_10DB: Final = 1.5

# Minimum noise multiplier for very strong signals.
# Prevents over-trusting very strong signals (which can still have noise).
# 0.5 means even very strong signals use at least 50% of base noise.
ADAPTIVE_MIN_NOISE_MULTIPLIER: Final = 0.5
