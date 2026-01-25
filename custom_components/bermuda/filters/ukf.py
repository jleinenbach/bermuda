"""
Unscented Kalman Filter for multi-scanner BLE RSSI fusion.

The UKF handles non-linear relationships between state and measurements
by using sigma points instead of linearization (as in EKF). This is
particularly useful for BLE positioning where:

1. Multiple scanners provide correlated measurements
2. The path-loss model (RSSI → distance) is non-linear
3. Partial observations occur (some scanners don't see the device)

This implementation tracks RSSI values from multiple scanners as a
state vector, enabling proper cross-correlation handling and optimal
fusion with learned fingerprint profiles.

Architecture:
    State: x = [rssi₁, rssi₂, ..., rssi_N] for N known scanners
    Process: RSSI drifts slowly (device movement between areas)
    Measurement: Observed RSSI from visible scanners (partial)
    Fingerprint: Compare state to learned area profiles (Mahalanobis)

References
----------
    - Julier, S.J., Uhlmann, J.K. (2004). Unscented filtering and nonlinear estimation
    - Wan, E.A., Van Der Merwe, R. (2000). The unscented Kalman filter for nonlinear estimation
    - Research: "Variational Bayesian Adaptive UKF for RSSI-based Indoor Localization"

Note on naming:
    Traditional Kalman notation uses P, Q, K, R for covariance, process noise,
    gain, and measurement noise. This implementation uses lowercase names
    (p_cov, q_noise, k_gain, r_noise) to comply with Python conventions.

"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .base import SignalFilter
from .const import (
    DEFAULT_UPDATE_DT,
    KALMAN_MEASUREMENT_NOISE,
    MAX_UPDATE_DT,
    MIN_UPDATE_DT,
)
from .ukf_numpy import (
    cholesky_numpy,
    is_numpy_available,
    matrix_inverse_numpy,
    matrix_multiply_numpy,
    sigma_points_numpy,
)

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from custom_components.bermuda.correlation.area_profile import AreaProfile
    from custom_components.bermuda.correlation.room_profile import RoomProfile


# =============================================================================
# UKF Constants
# =============================================================================

# UKF tuning parameters (van der Merwe defaults)
UKF_ALPHA: float = 0.001  # Spread of sigma points (small = tight around mean)
UKF_BETA: float = 2.0  # Prior knowledge about distribution (2 = Gaussian)
UKF_KAPPA: float = 0.0  # Secondary scaling parameter

# Process noise for multi-scanner state (per second)
# Higher than 1D Kalman because we're tracking multiple correlated values
UKF_PROCESS_NOISE_PER_SECOND: float = 0.5

# Measurement noise for RSSI observations
UKF_MEASUREMENT_NOISE: float = KALMAN_MEASUREMENT_NOISE

# Minimum variance to prevent numerical issues
MIN_VARIANCE: float = 0.01

# Default RSSI for unobserved scanners (very weak signal)
DEFAULT_RSSI: float = -100.0

# NumPy acceleration: Use NumPy for ALL matrix operations if available.
# This ensures consistent numerical behavior across all installations.
#
# Previous design used a threshold (n > 10), but this caused:
# - Inconsistent results between users with different scanner counts
# - Hard-to-debug "works for me" issues due to floating-point differences
# - Unnecessary complexity for minimal performance gain (0.1ms vs 0.01ms)
#
# New design: If NumPy is available, use it. Period.
# This gives consistent behavior: all NumPy users get identical results,
# all pure-Python users get identical results.
USE_NUMPY_IF_AVAILABLE: bool = True

# Minimum variance floor for fingerprint matching.
# Prevents "hyper-precision paradox" where converged Kalman filters
# have very low variance (2-5), causing normal BLE fluctuations (3-5 dB)
# to be rejected as massive deviations (2+ sigma).
#
# Value 25.0 corresponds to sigma = 5 dB, the upper end of typical BLE noise.
#
# IMPORTANT: This is SEPARATE from UKF_MEASUREMENT_NOISE (4.0) which is
# for per-sample Kalman updates. This floor applies when comparing a
# momentary UKF state against a long-term profile average, where the
# full measurement noise (not just estimation error) is relevant.
#
# Effect: With floor 25.0, a 3dB deviation → D² ≈ 0.36 → score ≈ 0.94
#         Without floor (var=4.5), 3dB deviation → D² ≈ 2.0 → score ≈ 0.72
UKF_MIN_MATCHING_VARIANCE: float = 25.0


def _cholesky_decompose(matrix: list[list[float]]) -> list[list[float]]:
    """
    Compute Cholesky decomposition L such that matrix = L @ L.T.

    Uses NumPy acceleration if available for consistent numerical behavior
    across all installations. Falls back to Cholesky-Banachiewicz algorithm.

    Args:
    ----
        matrix: Symmetric positive-definite matrix.

    Returns:
    -------
        Lower triangular matrix L.

    Note:
    ----
        Small regularization is automatically added for numerical stability.

    """
    # Try NumPy if available (consistent behavior for all users with NumPy)
    if USE_NUMPY_IF_AVAILABLE and is_numpy_available():
        result = cholesky_numpy(matrix)
        if result is not None:
            return result
        # Fall through to pure Python if NumPy fails

    n = len(matrix)

    # Pure Python implementation (Cholesky-Banachiewicz)
    lower = [[0.0] * n for _ in range(n)]

    for i in range(n):
        for j in range(i + 1):
            sum_k = sum(lower[i][k] * lower[j][k] for k in range(j))

            if i == j:
                val = matrix[i][i] - sum_k
                if val <= 0:
                    # Matrix not positive definite - add small regularization
                    val = MIN_VARIANCE
                lower[i][j] = math.sqrt(val)
            elif abs(lower[j][j]) < MIN_VARIANCE:
                # Near-zero diagonal: treat as zero to avoid numerical instability
                # Using tolerance check instead of exact equality (lower[j][j] == 0)
                lower[i][j] = 0.0
            else:
                lower[i][j] = (matrix[i][j] - sum_k) / lower[j][j]

    return lower


def _matrix_add(a: list[list[float]], b: list[list[float]], scale_b: float = 1.0) -> list[list[float]]:
    """Add two matrices: result = a + scale_b * b."""
    n = len(a)
    return [[a[i][j] + scale_b * b[i][j] for j in range(n)] for i in range(n)]


def _matrix_multiply(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    """
    Multiply two matrices.

    Uses NumPy acceleration if available for consistent behavior.
    """
    # Try NumPy if available
    if USE_NUMPY_IF_AVAILABLE and is_numpy_available():
        result = matrix_multiply_numpy(a, b)
        if result is not None:
            return result

    # Pure Python implementation
    n = len(a)
    m = len(b[0])
    k = len(b)
    return [[sum(a[i][p] * b[p][j] for p in range(k)) for j in range(m)] for i in range(n)]


def _matrix_transpose(a: list[list[float]]) -> list[list[float]]:
    """Transpose a matrix."""
    n = len(a)
    m = len(a[0])
    return [[a[j][i] for j in range(n)] for i in range(m)]


def _matrix_inverse(matrix: list[list[float]]) -> list[list[float]]:
    """
    Compute matrix inverse.

    Uses NumPy acceleration if available for consistent numerical behavior,
    otherwise uses Gauss-Jordan elimination.

    Args:
    ----
        matrix: Square matrix.

    Returns:
    -------
        Inverse matrix.

    Note:
    ----
        Small regularization is automatically added for numerical stability.

    """
    # Try NumPy if available
    if USE_NUMPY_IF_AVAILABLE and is_numpy_available():
        result = matrix_inverse_numpy(matrix)
        if result is not None:
            return result
        # Fall through to pure Python if NumPy fails

    n = len(matrix)

    # Pure Python implementation (Gauss-Jordan elimination)
    # Create augmented matrix [A | I]
    aug = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(matrix)]

    # Forward elimination
    for col in range(n):
        # Find pivot
        max_row = col
        for row in range(col + 1, n):
            if abs(aug[row][col]) > abs(aug[max_row][col]):
                max_row = row
        aug[col], aug[max_row] = aug[max_row], aug[col]

        # Check for singular matrix
        if abs(aug[col][col]) < 1e-10:
            # Add regularization
            aug[col][col] = MIN_VARIANCE

        # Eliminate column
        for row in range(n):
            if row != col:
                factor = aug[row][col] / aug[col][col]
                for j in range(2 * n):
                    aug[row][j] -= factor * aug[col][j]

    # Normalize rows
    for i in range(n):
        divisor = aug[i][i]
        for j in range(2 * n):
            aug[i][j] /= divisor

    # Extract inverse
    return [row[n:] for row in aug]


def _outer_product(a: list[float], b: list[float]) -> list[list[float]]:
    """Compute outer product of two vectors."""
    return [[a[i] * b[j] for j in range(len(b))] for i in range(len(a))]


def _identity_matrix(n: int, scale: float = 1.0) -> list[list[float]]:
    """Create scaled identity matrix."""
    return [[scale if i == j else 0.0 for j in range(n)] for i in range(n)]


@dataclass
class UnscentedKalmanFilter(SignalFilter):
    """
    Multi-scanner Unscented Kalman Filter for BLE RSSI fusion.

    Tracks RSSI values from multiple scanners as a state vector,
    properly handling cross-correlations and partial observations.

    The UKF uses sigma points to propagate uncertainty through
    non-linear transformations, making it suitable for:
    - Log-distance path loss models
    - Fingerprint matching with Mahalanobis distance
    - Sensor fusion with different noise characteristics

    Attributes:
    ----------
        scanner_addresses: List of scanner MAC addresses (defines state order)
        state: State vector [rssi₁, rssi₂, ..., rssi_N]
        covariance: Covariance matrix (N x N)

    Example:
    -------
        ukf = UnscentedKalmanFilter(scanner_addresses=["AA:BB:...", "CC:DD:..."])
        ukf.predict(dt=1.0)
        ukf.update({"AA:BB:...": -65.0, "CC:DD:...": -78.0})
        matches = ukf.match_fingerprints(area_profiles)

    """

    scanner_addresses: list[str] = field(default_factory=list)

    # State vector and covariance (internal, use properties for access)
    _x: list[float] = field(default_factory=list, repr=False)
    _p_cov: list[list[float]] = field(default_factory=list, repr=False)

    # UKF parameters
    alpha: float = UKF_ALPHA
    beta: float = UKF_BETA
    kappa: float = UKF_KAPPA

    # Noise parameters
    process_noise: float = UKF_PROCESS_NOISE_PER_SECOND
    measurement_noise: float = UKF_MEASUREMENT_NOISE

    # Sample count for interface compatibility
    sample_count: int = 0
    _initialized: bool = False

    # Time-aware filtering: track last timestamp for dt calculation
    _last_timestamp: float | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Initialize state vector and covariance if scanners provided."""
        if self.scanner_addresses and not self._initialized:
            self._initialize_state()

    def _initialize_state(self) -> None:
        """Initialize state vector and covariance matrix."""
        n = len(self.scanner_addresses)
        if n == 0:
            return

        # Initialize state to default RSSI (very weak signal)
        self._x = [DEFAULT_RSSI] * n

        # Initialize covariance with high uncertainty
        self._p_cov = _identity_matrix(n, self.measurement_noise * 10)

        self._initialized = True

    def add_scanner(self, address: str) -> int:
        """
        Add a new scanner to track.

        Args:
        ----
            address: Scanner MAC address

        Returns:
        -------
            Index of the scanner in the state vector

        """
        if address in self.scanner_addresses:
            return self.scanner_addresses.index(address)

        self.scanner_addresses.append(address)
        n = len(self.scanner_addresses)

        if not self._initialized:
            self._initialize_state()
        else:
            # Extend state vector
            self._x.append(DEFAULT_RSSI)

            # Extend covariance matrix
            high_var = self.measurement_noise * 10
            for row in self._p_cov:
                row.append(0.0)  # No correlation with new scanner initially
            self._p_cov.append([0.0] * (n - 1) + [high_var])

        return n - 1

    @property
    def n_scanners(self) -> int:
        """Return number of tracked scanners."""
        return len(self.scanner_addresses)

    @property
    def state(self) -> list[float]:
        """Return current state estimate."""
        return self._x.copy()

    @property
    def covariance(self) -> list[list[float]]:
        """Return current covariance matrix."""
        return [row.copy() for row in self._p_cov]

    def _compute_sigma_points(self) -> tuple[list[list[float]], list[float], list[float]]:
        """
        Compute sigma points for the UKF.

        Uses NumPy acceleration for large scanner networks if available.

        Returns
        -------
            Tuple of (sigma_points, weights_mean, weights_cov)
            - sigma_points: 2n+1 points, each of dimension n
            - weights_mean: weights for mean calculation
            - weights_cov: weights for covariance calculation

        """
        n = self.n_scanners
        if n == 0:
            return [], [], []

        # Scaling parameters
        lambda_ = self.alpha**2 * (n + self.kappa) - n
        gamma = math.sqrt(n + lambda_)

        # Weights
        w0_mean = lambda_ / (n + lambda_)
        w0_cov = w0_mean + (1 - self.alpha**2 + self.beta)
        wi = 1.0 / (2.0 * (n + lambda_))

        weights_mean = [w0_mean] + [wi] * (2 * n)
        weights_cov = [w0_cov] + [wi] * (2 * n)

        # Try NumPy if available (consistent behavior for all users)
        if USE_NUMPY_IF_AVAILABLE and is_numpy_available():
            sigma_points_np = sigma_points_numpy(self._x, self._p_cov, gamma)
            if sigma_points_np is not None:
                return sigma_points_np, weights_mean, weights_cov
            # Fall through to pure Python if NumPy fails

        # Pure Python implementation
        # Compute sqrt(P) using Cholesky decomposition
        try:
            sqrt_cov = _cholesky_decompose(self._p_cov)
        except ValueError:
            # Fallback: use diagonal sqrt
            sqrt_cov = _identity_matrix(n)
            for i in range(n):
                sqrt_cov[i][i] = math.sqrt(max(self._p_cov[i][i], MIN_VARIANCE))

        # Scale by gamma
        scaled_sqrt = [[gamma * sqrt_cov[i][j] for j in range(n)] for i in range(n)]

        # Generate sigma points: [x, x + sqrt(P), x - sqrt(P)]
        sigma_points: list[list[float]] = [self._x.copy()]

        for j in range(n):
            # x + column j of scaled sqrt(P)
            point_plus = [self._x[i] + scaled_sqrt[i][j] for i in range(n)]
            sigma_points.append(point_plus)

            # x - column j of scaled sqrt(P)
            point_minus = [self._x[i] - scaled_sqrt[i][j] for i in range(n)]
            sigma_points.append(point_minus)

        return sigma_points, weights_mean, weights_cov

    def predict(self, dt: float = 1.0) -> None:
        """
        Predict step: propagate state forward in time.

        For RSSI tracking, the process model is nearly static:
        RSSI values don't change unless the device moves.
        Process noise increases with time to allow for gradual drift.

        Args:
        ----
            dt: Time delta in seconds since last update

        """
        if not self._initialized or self.n_scanners == 0:
            return

        # Process noise scales with time
        # Longer time = more uncertainty (device might have moved)
        q_noise = _identity_matrix(self.n_scanners, self.process_noise * dt)

        # P = P + Q (covariance grows with process noise)
        self._p_cov = _matrix_add(self._p_cov, q_noise)

    def update(self, measurement: float, timestamp: float | None = None) -> float:
        """
        SignalFilter interface: update with single measurement.

        For multi-scanner UKF, this adds the measurement to internal buffer.
        Use update_multi() for proper multi-scanner updates.

        Args:
        ----
            measurement: RSSI value (not directly usable without scanner address)
            timestamp: Optional timestamp

        Returns:
        -------
            The measurement (unchanged, as we can't process without address)

        """
        self.sample_count += 1
        return measurement

    def update_multi(
        self,
        measurements: dict[str, float],
        timestamp: float | None = None,
    ) -> list[float]:
        """
        Update with multi-scanner RSSI measurements.

        Handles partial observations: if some scanners don't see the device,
        their state uncertainty grows but the overall estimate remains valid.

        Time-Aware Filtering:
            When timestamps are provided, automatically runs predict() with
            the calculated dt before the update step. This models uncertainty
            growth during the time between measurements.

        Args:
        ----
            measurements: Dict of scanner_address -> RSSI value
            timestamp: Optional timestamp (seconds). When provided, enables
                       time-aware filtering with automatic predict() call.

        Returns:
        -------
            Updated state vector

        """
        if not measurements:
            return self._x.copy()

        # Calculate dt for time-aware predict
        dt = DEFAULT_UPDATE_DT
        if timestamp is not None:
            if self._last_timestamp is not None:
                raw_dt = timestamp - self._last_timestamp
                dt = max(MIN_UPDATE_DT, min(raw_dt, MAX_UPDATE_DT))
            self._last_timestamp = timestamp

        # Ensure all scanners are tracked
        for addr in measurements:
            if addr not in self.scanner_addresses:
                self.add_scanner(addr)

        if not self._initialized:
            self._initialize_state()
            if timestamp is not None:
                self._last_timestamp = timestamp

        n = self.n_scanners
        self.sample_count += 1

        # Time-aware predict: grow uncertainty based on time since last update
        # This models the fact that longer gaps = more uncertainty
        self.predict(dt=dt)

        # Build measurement vector and observation matrix
        # Only include scanners that provided measurements
        observed_indices: list[int] = []
        z: list[float] = []

        for i, addr in enumerate(self.scanner_addresses):
            if addr in measurements:
                observed_indices.append(i)
                z.append(measurements[addr])

        if not observed_indices:
            return self._x.copy()

        m = len(observed_indices)  # Number of observations

        # Generate sigma points
        sigma_points, weights_mean, weights_cov = self._compute_sigma_points()

        # Transform sigma points through observation model
        # H selects observed components: z_sigma[k] = sigma_points[k][observed_indices]
        z_sigma = [[sp[i] for i in observed_indices] for sp in sigma_points]

        # Predicted measurement mean
        z_mean = [sum(weights_mean[k] * z_sigma[k][j] for k in range(len(sigma_points))) for j in range(m)]

        # Innovation covariance pzz = sum(w * (z - z_mean) @ (z - z_mean).T) + R
        pzz: list[list[float]] = [[0.0] * m for _ in range(m)]
        for k in range(len(sigma_points)):
            diff = [z_sigma[k][j] - z_mean[j] for j in range(m)]
            outer = _outer_product(diff, diff)
            for i in range(m):
                for j in range(m):
                    pzz[i][j] += weights_cov[k] * outer[i][j]

        # Add measurement noise
        r_noise = _identity_matrix(m, self.measurement_noise)
        pzz = _matrix_add(pzz, r_noise)

        # Cross-covariance pxz = sum(w * (x - x_mean) @ (z - z_mean).T)
        pxz: list[list[float]] = [[0.0] * m for _ in range(n)]
        for k in range(len(sigma_points)):
            x_diff = [sigma_points[k][i] - self._x[i] for i in range(n)]
            z_diff = [z_sigma[k][j] - z_mean[j] for j in range(m)]
            outer = _outer_product(x_diff, z_diff)
            for i in range(n):
                for j in range(m):
                    pxz[i][j] += weights_cov[k] * outer[i][j]

        # Kalman gain k_gain = pxz @ inv(pzz)
        try:
            pzz_inv = _matrix_inverse(pzz)
        except (ValueError, ZeroDivisionError):
            # Fallback: use diagonal inverse
            pzz_inv = _identity_matrix(m)
            for i in range(m):
                pzz_inv[i][i] = 1.0 / max(pzz[i][i], MIN_VARIANCE)

        k_gain = _matrix_multiply(pxz, pzz_inv)

        # Innovation
        innovation = [z[j] - z_mean[j] for j in range(m)]

        # Update state: x = x + K @ innovation
        for i in range(n):
            self._x[i] += sum(k_gain[i][j] * innovation[j] for j in range(m))

        # Update covariance: P = P - K @ Pzz @ K.T
        k_pzz = _matrix_multiply(k_gain, pzz)
        k_t = _matrix_transpose(k_gain)
        k_pzz_k_t = _matrix_multiply(k_pzz, k_t)
        self._p_cov = _matrix_add(self._p_cov, k_pzz_k_t, scale_b=-1.0)

        # Ensure P remains positive semi-definite (numerical stability)
        for i in range(n):
            self._p_cov[i][i] = max(self._p_cov[i][i], MIN_VARIANCE)

        return self._x.copy()

    def update_sequential(
        self,
        measurements: dict[str, float],
        timestamp: float | None = None,
    ) -> list[float]:
        """
        Update with multi-scanner RSSI measurements using sequential scalar updates.

        This is an alternative to update_multi() that processes observations
        one at a time using scalar Kalman equations. Benefits:
        - O(n²) per observation vs O(n³) for full matrix approach
        - Better for partial observations (only some scanners see device)
        - Numerically more stable for ill-conditioned covariance matrices

        The result is mathematically equivalent to update_multi() but may have
        small numerical differences due to floating point operations.

        Args:
        ----
            measurements: Dict of scanner_address -> RSSI value
            timestamp: Optional timestamp for time-aware filtering.

        Returns:
        -------
            Updated state vector

        """
        if not measurements:
            return self._x.copy()

        # Calculate dt for time-aware predict
        dt = DEFAULT_UPDATE_DT
        if timestamp is not None:
            if self._last_timestamp is not None:
                raw_dt = timestamp - self._last_timestamp
                dt = max(MIN_UPDATE_DT, min(raw_dt, MAX_UPDATE_DT))
            self._last_timestamp = timestamp

        # Ensure all scanners are tracked
        for addr in measurements:
            if addr not in self.scanner_addresses:
                self.add_scanner(addr)

        if not self._initialized:
            self._initialize_state()
            if timestamp is not None:
                self._last_timestamp = timestamp

        n = self.n_scanners
        self.sample_count += 1

        # Time-aware predict
        self.predict(dt=dt)

        # Process each observation sequentially using scalar Kalman equations
        for addr, rssi in measurements.items():
            i = self.scanner_addresses.index(addr)

            # Extract row i of covariance (P[i, :])
            p_row = self._p_cov[i]

            # Innovation variance (scalar): S = P[i,i] + R
            s = p_row[i] + self.measurement_noise

            # Avoid division by zero
            s = max(s, MIN_VARIANCE)

            # Kalman gain (vector): K = P[:, i] / S
            # Note: For symmetric P, P[:, i] = P[i, :] = p_row
            k = [p_row[j] / s for j in range(n)]

            # Innovation (scalar): y = z - x[i]
            innovation = rssi - self._x[i]

            # Update state: x = x + K * y
            for j in range(n):
                self._x[j] += k[j] * innovation

            # Update covariance: P = P - K @ K.T * S
            # This is equivalent to P = (I - K @ H) @ P for scalar observation
            for row in range(n):
                for col in range(n):
                    self._p_cov[row][col] -= k[row] * k[col] * s

        # Ensure P remains positive semi-definite
        for i in range(n):
            self._p_cov[i][i] = max(self._p_cov[i][i], MIN_VARIANCE)

        return self._x.copy()

    def match_fingerprints(
        self,
        area_profiles: dict[str, AreaProfile],
        room_profiles: dict[str, RoomProfile] | None = None,
    ) -> list[tuple[str, float, float]]:
        """
        Compare current UKF state to learned fingerprints.

        Uses both device-specific (AreaProfile) and device-independent (RoomProfile)
        fingerprints for matching. Combines scores using weighted fusion.

        Args:
        ----
            area_profiles: Dict of area_id -> AreaProfile with device-specific fingerprints
            room_profiles: Optional dict of area_id -> RoomProfile (device-independent)

        Returns:
        -------
            List of (area_id, mahalanobis_distance, match_score) sorted by score.

        """
        results: list[tuple[str, float, float]] = []

        # Get all area_ids from both profile types
        all_area_ids = set(area_profiles.keys())
        if room_profiles:
            all_area_ids |= set(room_profiles.keys())

        # Build current readings dict from UKF state for RoomProfile matching
        current_readings: dict[str, float] = {}
        for i, addr in enumerate(self.scanner_addresses):
            if i < len(self._x):
                current_readings[addr] = self._x[i]

        for area_id in all_area_ids:
            device_score: float | None = None
            device_samples = 0
            room_score: float | None = None

            # Device-specific matching (Mahalanobis distance)
            if area_id in area_profiles:
                profile = area_profiles[area_id]
                fp_mean: list[float] = []
                fp_var: list[float] = []
                state_indices: list[int] = []

                for i, addr in enumerate(self.scanner_addresses):
                    if hasattr(profile, "_absolute_profiles"):
                        abs_profiles = profile._absolute_profiles
                        if addr in abs_profiles:
                            abs_profile = abs_profiles[addr]
                            if hasattr(abs_profile, "is_mature") and abs_profile.is_mature:
                                fp_mean.append(abs_profile.expected_rssi)
                                fp_var.append(abs_profile.variance)
                                state_indices.append(i)
                                device_samples += abs_profile.sample_count

                if len(state_indices) >= 2:
                    x_sub = [self._x[i] for i in state_indices]
                    n_sub = len(state_indices)
                    p_sub = [
                        [self._p_cov[state_indices[i]][state_indices[j]] for j in range(n_sub)] for i in range(n_sub)
                    ]
                    combined_cov = [
                        [p_sub[i][j] + (fp_var[i] if i == j else 0.0) for j in range(n_sub)] for i in range(n_sub)
                    ]

                    # Apply variance floor to diagonal (BUG FIX: Hyper-Precision Paradox)
                    # Without this floor, converged filters produce combined_cov[i][i] ~ 4-5,
                    # making normal BLE fluctuations (3-5 dB) look like 2+ sigma deviations.
                    # The floor ensures realistic tolerance for RSSI variation.
                    for k in range(n_sub):
                        combined_cov[k][k] = max(combined_cov[k][k], UKF_MIN_MATCHING_VARIANCE)

                    diff = [x_sub[i] - fp_mean[i] for i in range(n_sub)]

                    try:
                        cov_inv = _matrix_inverse(combined_cov)
                        d_squared = sum(
                            diff[i] * sum(cov_inv[i][j] * diff[j] for j in range(n_sub)) for i in range(n_sub)
                        )
                        device_score = math.exp(-d_squared / (2 * n_sub))

                        # Debug logging for UKF matching diagnostics
                        _LOGGER.debug(
                            "UKF match area=%s: n=%d diff=%s d²=%.2f score=%.4f diag_before=%s diag_after=%s",
                            area_id,
                            n_sub,
                            [round(d, 1) for d in diff],
                            d_squared,
                            device_score,
                            [round(p_sub[k][k] + fp_var[k], 1) for k in range(n_sub)],
                            [round(combined_cov[k][k], 1) for k in range(n_sub)],
                        )
                    except (ValueError, ZeroDivisionError):
                        pass

            # Room-level matching (delta patterns)
            if room_profiles and area_id in room_profiles and len(current_readings) >= 2:
                room_profile = room_profiles[area_id]
                room_score = room_profile.get_match_score(current_readings)

            # Combine scores with weighted fusion
            if device_score is not None and room_score is not None:
                # Both available: weight by sample maturity
                device_weight = min(device_samples / 50.0, 1.0)
                room_weight = 1.0 - device_weight * 0.5  # Room always contributes
                total_weight = device_weight + room_weight
                combined_score = (device_score * device_weight + room_score * room_weight) / total_weight
                # Use device d² for sorting purposes
                d_squared = -2 * math.log(max(device_score, 0.001))
            elif device_score is not None:
                combined_score = device_score
                d_squared = -2 * math.log(max(device_score, 0.001))
            elif room_score is not None:
                # Room-only: use as fallback for new devices
                combined_score = room_score
                d_squared = -2 * math.log(max(room_score, 0.001))
            else:
                continue  # No data for this area

            results.append((area_id, d_squared, combined_score))

        return sorted(results, key=lambda x: -x[2])

    def get_estimate(self) -> float:
        """Return mean of state vector (for SignalFilter interface)."""
        if not self._x:
            return DEFAULT_RSSI
        return sum(self._x) / len(self._x)

    def get_variance(self) -> float:
        """Return average diagonal variance (for SignalFilter interface)."""
        if not self._p_cov:
            return self.measurement_noise
        n = len(self._p_cov)
        return sum(self._p_cov[i][i] for i in range(n)) / n

    def reset(self) -> None:
        """Reset filter to initial state."""
        self._x = []
        self._p_cov = []
        self.scanner_addresses = []
        self.sample_count = 0
        self._initialized = False
        self._last_timestamp = None

    def get_diagnostics(self) -> dict[str, Any]:
        """Return diagnostic information for debugging."""
        diag: dict[str, Any] = {
            "n_scanners": self.n_scanners,
            "sample_count": self.sample_count,
            "initialized": self._initialized,
        }

        if self._initialized and self.n_scanners > 0:
            diag["state"] = {addr: round(self._x[i], 1) for i, addr in enumerate(self.scanner_addresses)}
            diag["variances"] = {addr: round(self._p_cov[i][i], 2) for i, addr in enumerate(self.scanner_addresses)}
            diag["avg_variance"] = round(self.get_variance(), 2)

        return diag
