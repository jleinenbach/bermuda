"""General helper utilities for Bermuda."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Final

from homeassistant.helpers.device_registry import format_mac

from .const import MIN_DISTANCE, PATH_LOSS_EXPONENT_NEAR, TWO_SLOPE_BREAKPOINT_METRES


class KalmanFilter:
    """
    1D Kalman filter optimized for BLE RSSI signal filtering.

    Based on scientific research showing Kalman filtering improves BLE distance
    estimation accuracy by ~27% compared to raw measurements.

    References:
    - "The Influence of Kalman Filtering on RSSI in Multi-node BLE Communications" (2024)
    - "An Improved BLE Indoor Localization with Kalman-Based Fusion" (PMC5461075)
    - "A Practice of BLE RSSI Measurement for Indoor Positioning" (PMC8347277)

    The filter is applied to RSSI values (not distances) because RSSI measurements
    are linear, while distance calculations are logarithmic (non-linear).

    """

    def __init__(
        self,
        process_noise: float = 1.0,
        measurement_noise: float = 10.0,
        initial_estimate: float | None = None,
    ) -> None:
        """
        Initialize the Kalman filter.

        Args:
            process_noise (Q): How much the true value can change between updates.
                              Typical range: 0.5-2.0 for BLE RSSI. Higher values
                              make the filter more responsive but less smooth.
            measurement_noise (R): Variance of RSSI measurements.
                                   Typical RSSI std dev is 2-4 dBm, so R = 4-16.
                                   Higher values make filter trust measurements less.
            initial_estimate: Initial state estimate. If None, first measurement
                             will be used as the initial estimate.

        """
        # Kalman filter state
        self._estimate: float | None = initial_estimate  # Current state estimate (x)
        self._error_covariance: float = 1.0  # Estimation error covariance (P)

        # Filter parameters (tunable)
        self._process_noise: float = process_noise  # Process noise covariance (Q)
        self._measurement_noise: float = measurement_noise  # Measurement noise covariance (R)

        # For diagnostics
        self._kalman_gain: float = 0.0  # Last computed Kalman gain (K)
        self._measurement_count: int = 0

    def update(self, measurement: float) -> float:
        """
        Process a new measurement and return the filtered estimate.

        Args:
            measurement: New RSSI measurement in dBm

        Returns:
            Filtered RSSI estimate in dBm

        """
        self._measurement_count += 1

        # First measurement: initialize estimate
        if self._estimate is None:
            self._estimate = measurement
            self._error_covariance = self._measurement_noise
            self._kalman_gain = 1.0
            return self._estimate

        # Prediction step (assume stationary model: x_predicted = x_estimate)
        # P_predicted = P + Q
        predicted_error_covariance = self._error_covariance + self._process_noise

        # Update step
        # Kalman gain: K = P_predicted / (P_predicted + R)
        self._kalman_gain = predicted_error_covariance / (
            predicted_error_covariance + self._measurement_noise
        )

        # State estimate update: x = x + K * (measurement - x)
        self._estimate = self._estimate + self._kalman_gain * (measurement - self._estimate)

        # Error covariance update: P = (1 - K) * P_predicted
        self._error_covariance = (1.0 - self._kalman_gain) * predicted_error_covariance

        return self._estimate

    def reset(self, initial_estimate: float | None = None) -> None:
        """Reset the filter state."""
        self._estimate = initial_estimate
        self._error_covariance = 1.0
        self._kalman_gain = 0.0
        self._measurement_count = 0

    @property
    def estimate(self) -> float | None:
        """Current filtered estimate."""
        return self._estimate

    @property
    def kalman_gain(self) -> float:
        """Last computed Kalman gain (0-1). Higher = trusting measurement more."""
        return self._kalman_gain

    @property
    def is_initialized(self) -> bool:
        """Whether the filter has received at least one measurement."""
        return self._estimate is not None

    def set_parameters(self, process_noise: float | None = None, measurement_noise: float | None = None) -> None:
        """Update filter parameters dynamically."""
        if process_noise is not None:
            self._process_noise = process_noise
        if measurement_noise is not None:
            self._measurement_noise = measurement_noise

    def update_adaptive(
        self,
        measurement: float,
        rssi_strong_threshold: float = -50.0,
        noise_scale_per_10db: float = 2.0,
    ) -> float:
        """
        Process measurement with RSSI-adaptive measurement noise.

        Scientific basis: RSSI measurement variance increases with distance.
        Research shows SNR degrades as distance increases, meaning weaker
        signals have higher noise variance and should be trusted less.

        The measurement noise R is scaled based on signal strength:
        - At rssi_strong_threshold: uses base measurement_noise
        - For each 10 dB below threshold: multiplies noise by noise_scale_per_10db

        Formula: R_adaptive = R_base * scale^((threshold - rssi) / 10)

        This causes stronger signals to have more influence on the estimate,
        which aligns with the physical reality that stronger signals are
        more reliable measurements.

        Args:
            measurement: New RSSI measurement in dBm
            rssi_strong_threshold: RSSI level (dBm) where base noise applies.
                                   Default -50 dBm is typical strong indoor signal.
            noise_scale_per_10db: Noise multiplier per 10 dB signal decrease.
                                  Default 2.0 means noise doubles every 10 dB weaker.

        Returns:
            Filtered RSSI estimate in dBm

        References:
            - "Variational Bayesian Adaptive UKF for RSSI-based Indoor Localization"
            - PMC5461075: "An Improved BLE Indoor Localization with Kalman-Based Fusion"
        """
        # Calculate adaptive measurement noise based on signal strength
        db_below_threshold = rssi_strong_threshold - measurement
        if db_below_threshold > 0:
            # Weaker signal = higher noise (less trust)
            adaptive_noise = self._measurement_noise * (
                noise_scale_per_10db ** (db_below_threshold / 10.0)
            )
        else:
            # Stronger signal = use base noise or slightly less
            # Cap at 50% of base noise for very strong signals
            adaptive_noise = max(
                self._measurement_noise * 0.5,
                self._measurement_noise * (noise_scale_per_10db ** (db_below_threshold / 10.0))
            )

        # Store original and apply adaptive
        original_noise = self._measurement_noise
        self._measurement_noise = adaptive_noise

        # Run standard Kalman update
        result = self.update(measurement)

        # Restore original for next call
        self._measurement_noise = original_noise

        return result

MAC_PAIR_PATTERN: Final = re.compile(r"^[0-9A-Fa-f]{2}([:\-_][0-9A-Fa-f]{2}){5}$")
MAC_DOTTED_PATTERN: Final = re.compile(r"^[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}$")
MAC_BARE_PATTERN: Final = re.compile(r"^[0-9A-Fa-f]{12}$")
UUID_WITH_SUFFIX_PATTERN: Final = re.compile(
    r"([0-9A-Fa-f]{32}|[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})(.*)"
)


@lru_cache(64)
def mac_math_offset(mac: str | None, offset: int = 0) -> str | None:
    """
    Perform addition/subtraction on a MAC address.

    With a MAC address in xx:xx:xx:xx:xx:xx format,
    add the offset (which may be negative) to the
    last octet, and return the full new MAC.
    If the resulting octet is outside of 00-FF then
    the function returns None.
    """
    if mac is None:
        return None
    octet = mac[-2:]
    try:
        octet_int = bytes.fromhex(octet)[0]
    except ValueError:
        return None
    if 0 <= (octet_new := octet_int + offset) <= 255:
        return f"{mac[:-3]}:{(octet_new):02x}"
    return None


@lru_cache(1024)
def _mac_hex(mac: str) -> str | None:
    """Return hex-only mac string when the input matches a MAC format."""
    to_test = mac.strip()
    if MAC_PAIR_PATTERN.fullmatch(to_test):
        return re.sub(r"[^0-9A-Fa-f]", "", to_test).lower()
    if MAC_DOTTED_PATTERN.fullmatch(to_test):
        return to_test.replace(".", "").lower()
    if MAC_BARE_PATTERN.fullmatch(to_test):
        return to_test.lower()
    return None


@lru_cache(512)
def is_mac_address(mac: str) -> bool:
    """Return True when the provided string is a MAC-48 address."""
    return _mac_hex(mac) is not None


@lru_cache(512)
def normalize_mac(mac: str) -> str:
    """
    Format the mac address string using Home Assistant's canonical rules.

    Always returns lower-case, colon-delimited MACs or raises ValueError for
    non-MAC inputs.
    """
    formatted = format_mac(mac.strip())
    hex_only = _mac_hex(formatted)
    if hex_only is None:
        msg = f"'{mac}' is not a valid MAC address"
        raise ValueError(msg)
    return ":".join(hex_only[i : i + 2] for i in range(0, 12, 2))


@lru_cache(1024)
def normalize_identifier(identifier: str) -> str:
    """
    Canonicalise non-MAC identifiers (UUIDs, iBeacon ids, metadevice keys).

    UUIDs are lower-cased, hyphens are removed, and any suffix is preserved in
    lower-case. All other identifiers are lower-cased verbatim.
    """
    to_test = identifier.strip()
    match = UUID_WITH_SUFFIX_PATTERN.fullmatch(to_test)
    if match:
        uuid_hex = match.group(1).replace("-", "").lower()
        suffix = match.group(2).lower()
        return f"{uuid_hex}{suffix}"
    return to_test.lower()


@lru_cache(1024)
def mac_norm(mac: str) -> str:
    """
    Backwards-compatible address canonicaliser.

    Dispatches to normalize_mac for true MAC addresses, otherwise falls back to
    normalize_identifier for UUID-like and other pseudo identifiers.
    """
    if is_mac_address(mac):
        return normalize_mac(mac)
    return normalize_identifier(mac)


@lru_cache(1024)
def normalize_address(address: str) -> str:
    """Canonicalise addresses that may be MACs or pseudo identifiers."""
    if is_mac_address(address):
        return normalize_mac(address)
    return normalize_identifier(address)


@lru_cache(2048)
def mac_explode_formats(mac: str) -> set[str]:
    """
    Take a formatted mac address and return the formats
    likely to be found in our device info, adverts etc
    by replacing ":" with each of "", "-", "_", ".".

    For non-MAC identifiers, return only the canonicalised identifier.
    """
    altmacs = set()
    if not is_mac_address(mac):
        altmacs.add(normalize_identifier(mac))
        return altmacs

    _norm = normalize_mac(mac)
    altmacs.add(_norm)
    for newsep in ["", "-", "_", "."]:
        altmacs.add(_norm.replace(":", newsep))
    return altmacs


def mac_redact(mac: str, tag: str | None = None) -> str:
    """Remove the centre octets of a MAC and optionally replace with a tag."""
    if tag is None:
        tag = ":"
    return f"{mac[:2]}::{tag}::{mac[-2:]}"


@lru_cache(1024)
def rssi_to_metres(
    rssi: float,
    ref_power: float | None = None,
    attenuation: float | None = None,
) -> float:
    """
    Convert RSSI value to distance using Two-Slope Path Loss Model.

    This model provides significantly better accuracy than single-slope models
    by accounting for different propagation characteristics in near-field
    vs far-field regions. Research shows ~71% reduction in standard deviation
    (4.9 dB vs 17.2 dB) compared to single-slope models.

    Two-Slope Model:
    - Near-field (d < breakpoint): Uses PATH_LOSS_EXPONENT_NEAR (~1.8)
      Due to Fresnel zone clearance and waveguiding effects
    - Far-field (d >= breakpoint): Uses user-configured attenuation (~3.5)
      Due to multipath, obstacles, and environmental factors

    Args:
        rssi: Received signal strength indicator in dBm
        ref_power: RSSI at 1 metre reference distance (dBm). Affected by
                   receiver sensitivity, transmitter calibration, antenna design.
        attenuation: Far-field path loss exponent (typically 3.0-4.5 for indoor).
                     Only applies beyond TWO_SLOPE_BREAKPOINT_METRES (~6m).

    Returns:
        Distance in metres (minimum MIN_DISTANCE to prevent 0m readings).

    References:
        - PMC6165244: Indoor Positioning Algorithm Based on Improved RSSI Distance Model
        - Two-ray ground-reflection model (Wikipedia)
        - applsci-10-02003: BLE Indoor Localization research
    """
    if ref_power is None:
        message = "ref_power must be provided to compute distance"
        raise ValueError(message)
    if attenuation is None:
        message = "attenuation must be provided to compute distance"
        raise ValueError(message)

    # Near-field calculation using scientifically-derived exponent
    n_near = PATH_LOSS_EXPONENT_NEAR
    breakpoint = TWO_SLOPE_BREAKPOINT_METRES

    # Calculate distance assuming near-field propagation
    d_near = 10 ** ((ref_power - rssi) / (10 * n_near))

    if d_near <= breakpoint:
        # Signal strength indicates we're in near-field region
        distance = d_near
    else:
        # Far-field: transition to user-configured attenuation at breakpoint
        # Calculate RSSI that would be received at breakpoint distance
        rssi_at_breakpoint = ref_power - 10 * n_near * _log10(breakpoint)

        # Continue with far-field exponent from breakpoint onwards
        # d = breakpoint * 10^((rssi_bp - rssi) / (10 * n_far))
        distance = breakpoint * 10 ** ((rssi_at_breakpoint - rssi) / (10 * attenuation))

    # Ensure MIN_DISTANCE floor; handle non-numeric types gracefully (e.g., mocks in tests)
    try:
        return max(MIN_DISTANCE, distance)
    except TypeError:
        return distance


def _log10(x: float) -> float:
    """Calculate log base 10 using natural log for LRU cache compatibility."""
    import math
    return math.log10(x)


@lru_cache(256)
def clean_charbuf(instring: str | None) -> str:
    """
    Some people writing C on bluetooth devices seem to
    get confused between char arrays, strings and such. This
    function takes a potentially dodgy charbuf from a bluetooth
    device and cleans it of leading/trailing cruft
    and returns what's left, up to the first null, if any.

    If given None it returns an empty string.
    Characters trimmed are space, tab, CR, LF, NUL.
    """
    if instring is not None:
        return instring.strip(" \t\r\n\x00").split("\0")[0]
    return ""
