"""Tests for UKF Decision Margin (P1 improvement).

The decision margin measures how much better the best candidate is compared to
the second-best. A small margin indicates an uncertain decision where the system
should prefer stability over switching.

Formula: margin = (best_score - second_score) / best_score

Decision rules:
- margin >= 15%: Confident decision → normal threshold (0.30)
- margin < 15% AND current in top-2: Keep current area (hysteresis)
- margin < 15% AND current NOT in top-2: Use higher threshold (0.50)
"""

from __future__ import annotations

import pytest


class TestCalculateDecisionMargin:
    """Unit tests for _calculate_decision_margin() method."""

    def _make_handler(self):
        """Create a minimal AreaSelectionHandler for testing."""
        from unittest.mock import MagicMock

        from custom_components.bermuda.area_selection import AreaSelectionHandler

        handler = AreaSelectionHandler.__new__(AreaSelectionHandler)
        handler._coordinator = MagicMock()
        handler._coordinator.hass = MagicMock()
        handler._coordinator.ar = MagicMock()
        handler._coordinator.dr = MagicMock()
        handler._coordinator.options = {}
        handler._coordinator.config_entry = MagicMock()
        return handler

    def test_single_candidate_returns_full_confidence(self):
        """With only one candidate, return full margin (1.0) and confident=True."""
        handler = self._make_handler()
        scores = [("room_a", 0.75)]
        margin, is_confident = handler._calculate_decision_margin(scores)

        assert margin == 1.0
        assert is_confident is True

    def test_empty_list_returns_full_confidence(self):
        """Empty list should return full confidence (edge case)."""
        handler = self._make_handler()
        scores: list[tuple[str, float]] = []
        margin, is_confident = handler._calculate_decision_margin(scores)

        # With no scores, len < 2 → returns (1.0, True)
        assert margin == 1.0
        assert is_confident is True

    def test_confident_margin_above_threshold(self):
        """Margin >= 15% should be marked as confident."""
        handler = self._make_handler()
        # Kitchen: 0.75, Living Room: 0.50
        # margin = (0.75 - 0.50) / 0.75 = 33%
        scores = [("kitchen", 0.75), ("living_room", 0.50)]
        margin, is_confident = handler._calculate_decision_margin(scores)

        assert margin == pytest.approx(0.333, abs=0.01)
        assert is_confident is True

    def test_uncertain_margin_below_threshold(self):
        """Margin < 15% should be marked as uncertain."""
        handler = self._make_handler()
        # Kitchen: 0.75, Living Room: 0.72
        # margin = (0.75 - 0.72) / 0.75 = 4%
        scores = [("kitchen", 0.75), ("living_room", 0.72)]
        margin, is_confident = handler._calculate_decision_margin(scores)

        assert margin == pytest.approx(0.04, abs=0.01)
        assert is_confident is False

    def test_exact_threshold_is_confident(self):
        """Margin exactly at 15% should be marked as confident."""
        handler = self._make_handler()
        # best = 1.0, second = 0.85
        # margin = (1.0 - 0.85) / 1.0 = 0.15 = 15%
        scores = [("room_a", 1.0), ("room_b", 0.85)]
        margin, is_confident = handler._calculate_decision_margin(scores)

        assert margin == pytest.approx(0.15, abs=0.001)
        assert is_confident is True

    def test_zero_best_score_returns_no_confidence(self):
        """If best score is 0 or negative, return (0.0, False)."""
        handler = self._make_handler()
        scores = [("room_a", 0.0), ("room_b", -0.1)]
        margin, is_confident = handler._calculate_decision_margin(scores)

        assert margin == 0.0
        assert is_confident is False

    def test_negative_best_score_returns_no_confidence(self):
        """Negative best score (shouldn't happen, but handle gracefully)."""
        handler = self._make_handler()
        scores = [("room_a", -0.5), ("room_b", -0.8)]
        margin, is_confident = handler._calculate_decision_margin(scores)

        assert margin == 0.0
        assert is_confident is False

    def test_multiple_candidates_uses_top_two(self):
        """With multiple candidates, only top-2 are used for margin."""
        handler = self._make_handler()
        # First two determine margin, third is ignored
        scores = [
            ("kitchen", 0.80),
            ("living_room", 0.75),  # Used for margin
            ("bedroom", 0.30),  # Ignored
            ("bathroom", 0.10),  # Ignored
        ]
        margin, is_confident = handler._calculate_decision_margin(scores)

        # margin = (0.80 - 0.75) / 0.80 = 6.25%
        assert margin == pytest.approx(0.0625, abs=0.001)
        assert is_confident is False  # < 15%


class TestMarginInAreaTests:
    """Test that margin fields are populated in AreaTests."""

    def test_area_tests_has_margin_fields(self):
        """AreaTests dataclass should have ukf_margin and ukf_margin_confident."""
        from custom_components.bermuda.area_selection import AreaTests

        tests = AreaTests()

        # New fields should exist with None defaults
        assert hasattr(tests, "ukf_margin")
        assert hasattr(tests, "ukf_margin_confident")
        assert tests.ukf_margin is None
        assert tests.ukf_margin_confident is None

    def test_area_tests_to_dict_includes_margin(self):
        """AreaTests.to_dict() should include margin when ukf_match_score is set."""
        from custom_components.bermuda.area_selection import AreaTests

        tests = AreaTests()
        tests.decision_path = "UKF"
        tests.ukf_match_score = 0.75
        tests.ukf_margin = 0.33
        tests.ukf_margin_confident = True
        tests.ukf_threshold_used = 0.30
        tests.ukf_retention_mode = False
        tests.ukf_stickiness_applied = False

        result = tests.to_dict()

        assert result["ukf_margin"] == pytest.approx(0.33, abs=0.001)
        assert result["ukf_margin_confident"] is True

    def test_area_tests_sensortext_includes_margin(self):
        """AreaTests.sensortext() should show margin percentage."""
        from custom_components.bermuda.area_selection import AreaTests

        tests = AreaTests()
        tests.decision_path = "UKF"
        tests.ukf_match_score = 0.75
        tests.ukf_margin = 0.33
        tests.ukf_margin_confident = True
        tests.ukf_retention_mode = False
        tests.ukf_stickiness_applied = False

        text = tests.sensortext()

        # Should contain margin percentage: "Δ33%"
        assert "Δ33%" in text

    def test_area_tests_sensortext_shows_uncertain_indicator(self):
        """AreaTests.sensortext() should show warning for uncertain margin."""
        from custom_components.bermuda.area_selection import AreaTests

        tests = AreaTests()
        tests.decision_path = "UKF"
        tests.ukf_match_score = 0.75
        tests.ukf_margin = 0.04  # 4% - uncertain
        tests.ukf_margin_confident = False
        tests.ukf_retention_mode = False
        tests.ukf_stickiness_applied = False

        text = tests.sensortext()

        # Should contain margin with warning: "Δ4%⚠"
        assert "Δ4%⚠" in text


class TestMarginConstants:
    """Test that the new constants are defined correctly."""

    def test_ukf_min_decision_margin_exists(self):
        """UKF_MIN_DECISION_MARGIN should be defined as 0.15."""
        from custom_components.bermuda.const import UKF_MIN_DECISION_MARGIN

        assert UKF_MIN_DECISION_MARGIN == 0.15

    def test_ukf_uncertain_threshold_exists(self):
        """UKF_UNCERTAIN_THRESHOLD should be defined as 0.50."""
        from custom_components.bermuda.const import UKF_UNCERTAIN_THRESHOLD

        assert UKF_UNCERTAIN_THRESHOLD == 0.50

    def test_uncertain_threshold_higher_than_normal(self):
        """Uncertain threshold should be higher than normal threshold."""
        from custom_components.bermuda.const import (
            UKF_MIN_MATCH_SCORE,
            UKF_UNCERTAIN_THRESHOLD,
        )

        assert UKF_UNCERTAIN_THRESHOLD > UKF_MIN_MATCH_SCORE


class TestMarginHysteresisScenarios:
    """Test the hysteresis behavior based on margin and current area position."""

    def test_scenario_confident_margin_normal_switch(self):
        """
        Scenario: Confident margin (33%) → normal switching behavior.

        Kitchen: 0.75, Living Room: 0.50
        margin = 33% → CONFIDENT
        → Use normal threshold (0.30)
        → Kitchen wins (0.75 >= 0.30)
        """
        # This tests the expected behavior - actual integration test would need
        # the full coordinator setup. Here we just verify the margin calculation.
        from custom_components.bermuda.area_selection import AreaSelectionHandler

        handler = AreaSelectionHandler.__new__(AreaSelectionHandler)
        handler._coordinator = None  # Not used in this method

        scores = [("kitchen", 0.75), ("living_room", 0.50)]
        margin, is_confident = handler._calculate_decision_margin(scores)

        assert is_confident is True
        # Expected behavior: normal threshold (0.30) should be used

    def test_scenario_uncertain_margin_current_in_top2(self):
        """
        Scenario: Uncertain margin (4%), current area in top-2 → KEEP.

        Current: Living Room
        Kitchen: 0.75, Living Room: 0.72
        margin = 4% → UNCERTAIN
        Living Room in top-2 → KEEP (hysteresis)
        """
        from custom_components.bermuda.area_selection import AreaSelectionHandler

        handler = AreaSelectionHandler.__new__(AreaSelectionHandler)
        handler._coordinator = None

        scores = [("kitchen", 0.75), ("living_room", 0.72), ("bedroom", 0.15)]
        margin, is_confident = handler._calculate_decision_margin(scores)

        assert is_confident is False  # 4% < 15%
        assert margin == pytest.approx(0.04, abs=0.01)

        # Verify Living Room is in top-2
        top_2 = {scores[0][0], scores[1][0]}
        assert "living_room" in top_2

    def test_scenario_uncertain_margin_current_not_in_top2(self):
        """
        Scenario: Uncertain margin (4%), current NOT in top-2 → higher threshold.

        Current: Bedroom (not in top-2)
        Kitchen: 0.75, Living Room: 0.72, Bedroom: 0.15
        margin = 4% → UNCERTAIN
        Bedroom NOT in top-2 → Use UKF_UNCERTAIN_THRESHOLD (0.50)
        """
        from custom_components.bermuda.area_selection import AreaSelectionHandler

        handler = AreaSelectionHandler.__new__(AreaSelectionHandler)
        handler._coordinator = None

        scores = [("kitchen", 0.75), ("living_room", 0.72), ("bedroom", 0.15)]
        margin, is_confident = handler._calculate_decision_margin(scores)

        assert is_confident is False  # 4% < 15%

        # Verify Bedroom is NOT in top-2
        top_2 = {scores[0][0], scores[1][0]}
        assert "bedroom" not in top_2

        # Expected behavior: UKF_UNCERTAIN_THRESHOLD (0.50) should be used
        # Kitchen would still win (0.75 >= 0.50)
