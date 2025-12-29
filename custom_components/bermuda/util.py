"""General helper utilities for Bermuda."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Final

from homeassistant.helpers.device_registry import format_mac

from .const import MIN_DISTANCE


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
def rssi_to_metres(rssi: float, ref_power: float | None = None, attenuation: float | None = None) -> float:
    """
    Convert instant rssi value to a distance in metres.

    Based on the information from
    https://mdpi-res.com/d_attachment/applsci/applsci-10-02003/article_deploy/applsci-10-02003.pdf?version=1584265508

    attenuation:    a factor representing environmental attenuation
                    along the path. Will vary by humidity, terrain etc.
    ref_power:      db. measured rssi when at 1m distance from rx. The will
                    be affected by both receiver sensitivity and transmitter
                    calibration, antenna design and orientation etc.

    Returns a minimum of MIN_DISTANCE (0.1m) to prevent multiple sensors
    from appearing at "0m" when signals are very strong.
    """
    if ref_power is None:
        message = "ref_power must be provided to compute distance"
        raise ValueError(message)
    if attenuation is None:
        message = "attenuation must be provided to compute distance"
        raise ValueError(message)

    distance = 10 ** ((ref_power - rssi) / (10 * attenuation))
    # Ensure MIN_DISTANCE floor; handle non-numeric types gracefully (e.g., mocks in tests)
    try:
        return max(MIN_DISTANCE, distance)
    except TypeError:
        return distance


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
