"""
NumPy-accelerated UKF operations for large scanner networks.

This module provides optional NumPy-accelerated implementations of
computationally expensive matrix operations used by the UKF. These
functions are 10-100x faster than pure Python for large matrices.

The module uses lazy importing to avoid requiring NumPy as a dependency.
If NumPy is not available, all functions return None and the UKF falls
back to pure Python implementations.

Usage:
    from .ukf_numpy import (
        cholesky_numpy,
        matrix_inverse_numpy,
        mahalanobis_distance_numpy,
        is_numpy_available,
    )

    # Check if acceleration is available
    if is_numpy_available():
        result = cholesky_numpy(matrix)
    else:
        result = pure_python_cholesky(matrix)

Performance comparison (n=20 scanners):
    - Pure Python Cholesky: ~3ms
    - NumPy Cholesky: ~0.03ms (100x faster)

References
----------
    - NumPy linalg: https://numpy.org/doc/stable/reference/routines.linalg.html

"""

from __future__ import annotations

import logging
from typing import Any, cast

_LOGGER = logging.getLogger(__name__)

# Lazy import for NumPy - avoids requiring it as a dependency
_numpy: Any = None
_numpy_checked: bool = False


def _get_numpy() -> Any:
    """
    Lazy import numpy, return None if unavailable.

    Uses module-level caching to avoid repeated import attempts.

    Returns
    -------
        numpy module if available, None otherwise.

    """
    global _numpy, _numpy_checked  # noqa: PLW0603

    if _numpy_checked:
        return _numpy

    try:
        import numpy as np

        _numpy = np
        _LOGGER.debug("NumPy backend available for UKF acceleration (version %s)", np.__version__)
    except ImportError:
        _numpy = None
        _LOGGER.debug("NumPy not available, UKF will use pure Python (slower for large scanner networks)")

    _numpy_checked = True
    return _numpy


def is_numpy_available() -> bool:
    """
    Check if NumPy is available for acceleration.

    Returns
    -------
        True if NumPy can be imported, False otherwise.

    """
    return _get_numpy() is not None


def cholesky_numpy(matrix: list[list[float]]) -> list[list[float]] | None:
    """
    NumPy-accelerated Cholesky decomposition.

    Computes the lower triangular matrix L such that matrix = L @ L.T.

    Args:
    ----
        matrix: Symmetric positive-definite matrix as list of lists.

    Returns:
    -------
        Lower triangular matrix as list of lists, or None if NumPy
        is unavailable or the matrix is not positive definite.

    Note:
    ----
        Small regularization (1e-6) is added to the diagonal to handle
        near-singular matrices common in BLE RSSI covariance estimation.

    """
    np = _get_numpy()
    if np is None:
        return None

    try:
        arr = np.array(matrix, dtype=np.float64)
        n = arr.shape[0]

        # Add small regularization for numerical stability
        # BLE RSSI covariance matrices can be near-singular
        arr += np.eye(n) * 1e-6

        lower = np.linalg.cholesky(arr)
        return cast("list[list[float]]", lower.tolist())

    except np.linalg.LinAlgError:
        _LOGGER.debug("Cholesky decomposition failed (matrix not positive definite)")
        return None


def matrix_inverse_numpy(matrix: list[list[float]]) -> list[list[float]] | None:
    """
    NumPy-accelerated matrix inverse.

    Args:
    ----
        matrix: Square matrix as list of lists.

    Returns:
    -------
        Inverse matrix as list of lists, or None if NumPy is unavailable
        or the matrix is singular.

    Note:
    ----
        Small regularization (1e-6) is added to the diagonal to handle
        near-singular matrices.

    """
    np = _get_numpy()
    if np is None:
        return None

    try:
        arr = np.array(matrix, dtype=np.float64)
        n = arr.shape[0]

        # Add small regularization for numerical stability
        arr += np.eye(n) * 1e-6

        inv = np.linalg.inv(arr)
        return cast("list[list[float]]", inv.tolist())

    except np.linalg.LinAlgError:
        _LOGGER.debug("Matrix inversion failed (singular matrix)")
        return None


def mahalanobis_distance_numpy(
    diff: list[float],
    cov_inv: list[list[float]],
) -> float | None:
    """
    NumPy-accelerated Mahalanobis distance calculation.

    Computes: D² = diff.T @ cov_inv @ diff

    Args:
    ----
        diff: Difference vector (x - mean).
        cov_inv: Inverse covariance matrix.

    Returns:
    -------
        Squared Mahalanobis distance, or None if NumPy is unavailable.

    """
    np = _get_numpy()
    if np is None:
        return None

    diff_arr = np.array(diff, dtype=np.float64)
    cov_inv_arr = np.array(cov_inv, dtype=np.float64)

    # D² = diff.T @ Σ⁻¹ @ diff
    return float(diff_arr @ cov_inv_arr @ diff_arr)


def matrix_multiply_numpy(
    a: list[list[float]],
    b: list[list[float]],
) -> list[list[float]] | None:
    """
    NumPy-accelerated matrix multiplication.

    Args:
    ----
        a: First matrix (n x m).
        b: Second matrix (m x k).

    Returns:
    -------
        Product matrix (n x k), or None if NumPy is unavailable.

    """
    np = _get_numpy()
    if np is None:
        return None

    a_arr = np.array(a, dtype=np.float64)
    b_arr = np.array(b, dtype=np.float64)

    return cast("list[list[float]]", (a_arr @ b_arr).tolist())


def outer_product_numpy(
    a: list[float],
    b: list[float],
) -> list[list[float]] | None:
    """
    NumPy-accelerated outer product.

    Args:
    ----
        a: First vector (n,).
        b: Second vector (m,).

    Returns:
    -------
        Outer product matrix (n x m), or None if NumPy is unavailable.

    """
    np = _get_numpy()
    if np is None:
        return None

    a_arr = np.array(a, dtype=np.float64)
    b_arr = np.array(b, dtype=np.float64)

    return cast("list[list[float]]", np.outer(a_arr, b_arr).tolist())


def sigma_points_numpy(
    x: list[float],
    p_cov: list[list[float]],
    gamma: float,
) -> list[list[float]] | None:
    """
    NumPy-accelerated sigma point generation.

    Generates 2n+1 sigma points for the Unscented Transform.

    Args:
    ----
        x: State vector (n,).
        p_cov: Covariance matrix (n x n).
        gamma: Scaling parameter sqrt(n + lambda).

    Returns:
    -------
        Sigma points as list of lists (2n+1 x n), or None if NumPy
        is unavailable or Cholesky fails.

    """
    np = _get_numpy()
    if np is None:
        return None

    try:
        x_arr = np.array(x, dtype=np.float64)
        p_arr = np.array(p_cov, dtype=np.float64)
        n = len(x)

        # Add regularization
        p_arr += np.eye(n) * 1e-6

        # Cholesky decomposition
        sqrt_p = np.linalg.cholesky(p_arr)
        scaled_sqrt = gamma * sqrt_p

        # Generate sigma points: [x, x + cols, x - cols]
        sigma_points = [x_arr.tolist()]

        for j in range(n):
            col = scaled_sqrt[:, j]
            sigma_points.append((x_arr + col).tolist())
            sigma_points.append((x_arr - col).tolist())

    except np.linalg.LinAlgError:
        return None

    return sigma_points
