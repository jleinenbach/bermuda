"""
Confidence calculation from z-scores.

Pure functions with no external dependencies - easy to test and verify.
These functions convert statistical z-scores into confidence values
that can be used to adjust area selection decisions.
"""

from __future__ import annotations


def z_scores_to_confidence(
    z_scores: list[tuple[str, float]],
    threshold: float = 2.5,
) -> float:
    """
    Convert z-scores to confidence value (0.0 to 1.0).

    Uses a sigmoid-like function that maps average z-score to confidence:
    - z=0 -> confidence=1.0 (perfect match)
    - z=threshold -> confidence=0.5 (uncertain)
    - z->infinity -> confidence->0.0 (definitely wrong)

    Args:
        z_scores: List of (scanner_address, z_score) tuples.
        threshold: Z-score at which confidence drops to 0.5.
                  Default 2.5 means ~98.8% of normal observations
                  would have higher confidence.

    Returns:
        Confidence value between 0.0 and 1.0.
        Returns 1.0 if z_scores is empty (no data = no penalty).

    """
    if not z_scores:
        return 1.0

    avg_z = sum(z for _, z in z_scores) / len(z_scores)

    # Cauchy-like sigmoid: 1 / (1 + (z/threshold)^2)
    # Smoother falloff than Gaussian, handles outliers better
    return 1.0 / (1.0 + (avg_z / threshold) ** 2)


def weighted_z_scores_to_confidence(
    z_scores: list[tuple[str, float, int]],
    threshold: float = 2.5,
) -> float:
    """
    Convert z-scores to confidence, weighting by sample count.

    Correlations with more samples are more reliable and should
    have proportionally more influence on the confidence score.

    Args:
        z_scores: List of (scanner_address, z_score, sample_count) tuples.
        threshold: Z-score at which confidence drops to 0.5.

    Returns:
        Confidence value between 0.0 and 1.0.
        Returns 1.0 if z_scores is empty or total weight is zero.

    """
    if not z_scores:
        return 1.0

    total_weight = sum(samples for _, _, samples in z_scores)
    if total_weight == 0:
        return 1.0

    weighted_avg_z = sum(z * samples for _, z, samples in z_scores) / total_weight

    return 1.0 / (1.0 + (weighted_avg_z / threshold) ** 2)
