"""
Tests for confidence calculation.

These tests verify the mathematical properties of z-score to confidence
conversion, which is critical for correct area selection decisions.
"""

from __future__ import annotations

import pytest

from custom_components.bermuda.correlation.confidence import (
    weighted_z_scores_to_confidence,
    z_scores_to_confidence,
)


class TestZScoresToConfidence:
    """Tests for z-score to confidence conversion."""

    def test_empty_z_scores_returns_full_confidence(self) -> None:
        """No data means no penalty - return full confidence."""
        confidence = z_scores_to_confidence([])

        assert confidence == 1.0, (
            f"Empty z_scores returned confidence={confidence}, expected 1.0. "
            f"Penalizing when there's no correlation data would incorrectly "
            f"reduce confidence for all area candidates equally."
        )

    def test_perfect_match_returns_high_confidence(self) -> None:
        """Z-scores near zero should return confidence near 1.0."""
        z_scores = [
            ("scanner_a", 0.1),
            ("scanner_b", 0.2),
            ("scanner_c", 0.15),
        ]

        confidence = z_scores_to_confidence(z_scores)

        assert confidence > 0.95, (
            f"Near-zero z-scores (avg=0.15) returned confidence={confidence:.3f}. "
            f"Expected >0.95. Low confidence for matching patterns would cause "
            f"unnecessary area switching when device is in correct area."
        )

    def test_threshold_z_score_returns_half_confidence(self) -> None:
        """Z-score at threshold should return approximately 0.5 confidence."""
        threshold = 2.5
        z_scores = [("scanner_a", threshold)]

        confidence = z_scores_to_confidence(z_scores, threshold=threshold)

        assert 0.45 < confidence < 0.55, (
            f"Z-score at threshold ({threshold}) returned confidence={confidence:.3f}. "
            f"Expected ~0.5. The threshold parameter should be the inflection point "
            f"where confidence drops to 50%."
        )

    def test_high_z_scores_return_low_confidence(self) -> None:
        """Very high z-scores should return confidence approaching zero."""
        z_scores = [
            ("scanner_a", 5.0),
            ("scanner_b", 6.0),
            ("scanner_c", 5.5),
        ]

        confidence = z_scores_to_confidence(z_scores, threshold=2.5)

        assert confidence < 0.3, (
            f"High z-scores (avg=5.5) returned confidence={confidence:.3f}. "
            f"Expected <0.3. High confidence despite pattern mismatch would "
            f"leave devices assigned to wrong areas."
        )

    def test_single_outlier_diluted_by_good_matches(self) -> None:
        """One bad correlation shouldn't tank overall confidence if others match."""
        z_scores = [
            ("scanner_a", 0.2),  # Good match
            ("scanner_b", 0.3),  # Good match
            ("scanner_c", 4.0),  # Outlier!
            ("scanner_d", 0.25),  # Good match
        ]

        confidence = z_scores_to_confidence(z_scores, threshold=2.5)

        # avg_z = (0.2 + 0.3 + 4.0 + 0.25) / 4 = 1.1875
        assert confidence > 0.6, (
            f"One outlier among good matches returned confidence={confidence:.3f}. "
            f"Expected >0.6 (avg_z=1.19). Single-scanner anomalies (interference, "
            f"obstruction) shouldn't override multiple good correlations."
        )

    def test_confidence_is_monotonic_with_z_score(self) -> None:
        """Higher average z-score should always give lower confidence."""
        prev_confidence = 1.0

        for avg_z in [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]:
            z_scores = [("scanner", avg_z)]
            confidence = z_scores_to_confidence(z_scores)

            assert confidence <= prev_confidence, (
                f"Confidence increased from {prev_confidence:.3f} to {confidence:.3f} "
                f"as z-score increased to {avg_z}. Confidence must monotonically "
                f"decrease with z-score - worse match should never increase confidence."
            )
            prev_confidence = confidence

    def test_confidence_bounded_zero_to_one(self) -> None:
        """Confidence should always be between 0 and 1."""
        test_cases = [
            [],
            [("s", 0.0)],
            [("s", 100.0)],
            [("s", -1.0)],  # Negative z-score (edge case)
        ]

        for z_scores in test_cases:
            confidence = z_scores_to_confidence(z_scores)
            assert 0.0 <= confidence <= 1.0, (
                f"Confidence={confidence} is outside [0, 1] for z_scores={z_scores}. "
                f"Out-of-bounds confidence would break downstream calculations."
            )


class TestWeightedZScoresToConfidence:
    """Tests for sample-count-weighted confidence calculation."""

    def test_high_sample_correlation_weighted_more(self) -> None:
        """Correlations with more samples should have more influence."""
        # Scanner A: low z-score, many samples (reliable, good match)
        # Scanner B: high z-score, few samples (unreliable outlier)
        z_scores = [
            ("scanner_a", 0.5, 500),  # Good match, well-established
            ("scanner_b", 4.0, 50),  # Bad match, but fewer samples
        ]

        weighted = weighted_z_scores_to_confidence(z_scores, threshold=2.5)
        unweighted = z_scores_to_confidence([(s, z) for s, z, _ in z_scores], threshold=2.5)

        assert weighted > unweighted, (
            f"Weighted confidence ({weighted:.3f}) should exceed unweighted "
            f"({unweighted:.3f}) when reliable scanner (500 samples) shows good "
            f"match. Ignoring sample counts treats unreliable correlations as "
            f"equally trustworthy as well-established ones."
        )

    def test_empty_weighted_returns_full_confidence(self) -> None:
        """Empty z-scores return 1.0 confidence."""
        confidence = weighted_z_scores_to_confidence([])

        assert confidence == 1.0, f"Empty weighted z_scores returned {confidence}, expected 1.0."

    def test_zero_total_weight_returns_full_confidence(self) -> None:
        """Zero total sample count returns 1.0 to avoid division by zero."""
        z_scores = [
            ("scanner_a", 5.0, 0),
            ("scanner_b", 5.0, 0),
        ]

        confidence = weighted_z_scores_to_confidence(z_scores)

        assert confidence == 1.0, (
            f"Zero total weight returned confidence={confidence}, expected 1.0. "
            f"Division by zero would cause crash or NaN propagation."
        )

    def test_equal_weights_matches_unweighted(self) -> None:
        """With equal sample counts, weighted should match unweighted."""
        z_scores_weighted = [
            ("scanner_a", 1.0, 100),
            ("scanner_b", 2.0, 100),
            ("scanner_c", 1.5, 100),
        ]
        z_scores_unweighted = [(s, z) for s, z, _ in z_scores_weighted]

        weighted = weighted_z_scores_to_confidence(z_scores_weighted, threshold=2.5)
        unweighted = z_scores_to_confidence(z_scores_unweighted, threshold=2.5)

        assert abs(weighted - unweighted) < 0.001, (
            f"Equal weights: weighted={weighted:.4f} vs unweighted={unweighted:.4f}. "
            f"With equal sample counts, both methods should produce identical results."
        )
